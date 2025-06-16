import pandas as pd
import numpy as np
import hashlib
import hmac
from typing import List, Tuple
from scipy.stats import chi2_contingency
import plotly.graph_objects as go
from db.models import *
from bitcoin import update_bitcoins

# from bitcoinlib.services.services import Service

NUM_BLOCKCHAIN = 144  # Number of blockchain hashes per group
# BLUE_BALL_MAX = 36
# RED_BALL_MAX = 12
BLUE_BALL_MAX = 69
RED_BALL_MAX = 26
IS_UPDATE_DRAW = True  # Whether to update the draw automatically
IS_UPDATE_BITCOIN = True  # Whether to update the Bitcoin blocks automatically

# def getBitcoinBlockList(filename):
#     csv_blockchain = pd.read_csv(f'./input/{filename}.csv', dtype=str)
#     list_blockchain = list(csv_blockchain['hash'])
#     time_blockchain = list(csv_blockchain['timestamp'])
#     return list_blockchain, time_blockchain


def deterministic_rng(seed: bytes, count: int) -> List[int]:
    """
    Deterministic random number generator based on HMAC-SHA256.
    
    Args:
        seed: Seed bytes.
        count: Number of random numbers to generate.
    
    Returns:
        List[int]: The specified number of random integers.
    """
    random_numbers = []
    counter = 0
    while len(random_numbers) < count:
        h = hmac.new(seed, str(counter).encode('utf-8'), hashlib.sha256)
        random_bytes = h.digest()
        random_int = int.from_bytes(random_bytes, 'big')
        random_numbers.append(random_int)
        counter += 1
    return random_numbers

def generate_lotto_numbers_bitcoin(hashes: List[str]) -> Tuple[List[int], int]:
    """
    Generate lottery numbers using 144 Bitcoin block hashes.
    
    Args:
        hashes: List of 144 SHA-256 block hashes (each hash is a 64-character hex string).
        # timestamp: Optional timestamp (string format) as extra entropy.
    
    Returns:
        Tuple[List[int], int]: Front area numbers (5 sorted integers from 1 to BLUE_BALL_MAX), back area number (1 integer from 1 to RED_BALL_MAX).
    """
    if len(hashes) != NUM_BLOCKCHAIN:
        raise ValueError(f"{NUM_BLOCKCHAIN} block hashes must be provided")
    
    # Step 1: Concatenate all hashes
    combined = ''.join(hashes)

    # # Step 2: (Optional) Add timestamp as extra entropy
    # if timestamp:
    #     combined += timestamp
    
    # Step 3: Generate seed (SHA-256 hash)
    seed = hashlib.sha256(combined.encode('utf-8')).digest() 
        
    # Step 4: Generate random numbers
    random_numbers = deterministic_rng(seed, 6)  # Need 6 random numbers (5 front + 1 back)
    
    # Step 5: Generate front area numbers (5 unique numbers from 1 to BLUE_BALL_MAX)
    front_pool = list(range(1, BLUE_BALL_MAX+1))
    front_numbers = []
    for i in range(5):
        index = random_numbers[i] % len(front_pool)
        front_numbers.append(front_pool.pop(index))
    front_numbers.sort()
    
    # Step 6: Generate back area number (1 number from 1 to RED_BALL_MAX)
    back_number = (random_numbers[5] % RED_BALL_MAX) + 1
    
    return front_numbers, back_number

def verify_lotto_numbers(hashes: List[str], front: List[int], back: int) -> bool:
    """
    Verify if the lottery numbers are generated from the given block hashes.
    
    Args:
        hashes: 144 block hashes.
        front: Front area numbers.
        back: Back area number.
        # timestamp: Optional timestamp.
    
    Returns:
        bool: Whether the verification passed.
    """
    expected_front, expected_back = generate_lotto_numbers_bitcoin(hashes)
    return expected_front == front and expected_back == back


# Chi-square test function
def chi_square_test(numbers: List[int], num_categories: int, expected_freq: float) -> Tuple[float, float]:
    """
    Perform chi-square test to check if numbers are uniformly distributed.
    
    Args:
        numbers: List of numbers (e.g., all front area numbers).
        num_categories: Number range (BLUE_BALL_MAX for front, RED_BALL_MAX for back).
        expected_freq: Expected frequency for each number.
    
    Returns:
        Tuple[float, float]: Chi-square statistic and p-value.
    """
    # Count observed frequencies
    observed, _ = np.histogram(numbers, bins=num_categories, range=(1, num_categories + 1))
    expected = np.array([expected_freq] * num_categories)
    
    # Perform chi-square test
    chi2_stat, p_value, _, _ = chi2_contingency([observed, expected], correction=False)
    return chi2_stat, p_value

# Plotting function
def plot_distribution(front_all, back_all, total_draws):
    """
    Plot number distribution charts.
    
    Args:
        front_all: List of all front area numbers.
        back_all: List of all back area numbers.
        temp: Number of draws.
    """
    # Front area chi-square test
    front_expected_freq = (total_draws * 5) / BLUE_BALL_MAX  # 5 numbers per draw, BLUE_BALL_MAX total numbers
    front_chi2, front_p = chi_square_test(front_all, BLUE_BALL_MAX, front_expected_freq)
    front_stats = {
        "chi2": round(front_chi2, 2),
        "p_value": round(front_p, 4),
        "conclusion": "Uniform distribution (good randomness)" if front_p > 0.05 else "Non-uniform distribution (possible bias)"
    }

    
    # Back area chi-square test
    back_expected_freq = total_draws / RED_BALL_MAX  # 1 number per draw, RED_BALL_MAX total numbers
    back_chi2, back_p = chi_square_test(back_all, RED_BALL_MAX, back_expected_freq)
    back_stats = {
        "chi2": round(back_chi2, 2),
        "p_value": round(back_p, 4),
        "conclusion": "Uniform distribution (good randomness)" if back_p > 0.05 else "Non-uniform distribution (possible bias)"
    }

    # Generate front area Plotly chart
    front_freq, _ = np.histogram(front_all, bins=BLUE_BALL_MAX, range=(1, BLUE_BALL_MAX+1))
    fig_front = go.Figure()
    fig_front.add_trace(go.Bar(x=list(range(1, BLUE_BALL_MAX+1)), y=front_freq, name='Observed Frequency'))
    fig_front.add_hline(y=front_expected_freq, line_dash="dash", line_color="red", annotation_text="Expected Frequency")
    fig_front.update_layout(
        title="Front Area Number Frequency Distribution",
        xaxis_title="Number",
        yaxis_title="Frequency",
        showlegend=True
    )
    fig_front.write_html("static/front_plot.html", full_html=False)

    # Generate back area Plotly chart
    back_freq, _ = np.histogram(back_all, bins=RED_BALL_MAX, range=(1, RED_BALL_MAX+1))
    fig_back = go.Figure()
    fig_back.add_trace(go.Bar(x=list(range(1, RED_BALL_MAX+1)), y=back_freq, name='Observed Frequency'))
    fig_back.add_hline(y=back_expected_freq, line_dash="dash", line_color="red", annotation_text="Expected Frequency")
    fig_back.update_layout(
        title="Back Area Number Frequency Distribution",
        xaxis_title="Number",
        yaxis_title="Frequency",
        showlegend=True
    )
    fig_back.write_html("static/back_plot.html", full_html=False)

    return front_stats, back_stats


# def generate_lottery_numbers():
#     """
#     Generate lottery numbers.
    
#     Returns:
#         Tuple[List[int], int]: Front area numbers (5 sorted integers from 1 to BLUE_BALL_MAX), back area number (1 integer from 1 to RED_BALL_MAX).
#     """
#     blockchain = []
#     time_blockchain = []
#     blockchain, time_blockchain = getBitcoinBlockList('blockchain_timeup898560')

#     total_draws = int(len(blockchain) / NUM_BLOCKCHAIN)

#     list_lotto = []
    
#     for i in range(0, total_draws):    
#         hashes = blockchain[i * NUM_BLOCKCHAIN: (i + 1) * NUM_BLOCKCHAIN]        
#         # Generate numbers
#         front, back = generate_lotto_numbers_bitcoin(hashes)
#         list_lotto.append((i, front, back, time_blockchain[(i + 1) * NUM_BLOCKCHAIN - 1]))

#     front_all = [num for lotto in list_lotto for num in lotto[1]]
#     back_all = [lotto[2] for lotto in list_lotto]
#     # Plot number distribution charts
#     front_stats, back_stats = plot_distribution(front_all, back_all, total_draws)

#     return list_lotto, front_stats, back_stats


# def get_draw_id_by_height(height: int) -> int:
#     """
#     Get the lottery draw ID by blockchain height.
    
#     Args:
#         blockchain_height: Blockchain height.
    
#     Returns:
#         int: Lottery draw ID.
#     """
#     draw_id = height // NUM_BLOCKCHAIN + 1
#     return draw_id

def get_heights_by_draw_id(draw_id: int) -> List[int]:
    """
    Get the blockchain heights for a specific lottery draw ID.
    
    Args:
        draw_id: Lottery draw ID.
    
    Returns:
        List[int]: List of blockchain heights for the specified draw ID.
    """
    start_height = draw_id * NUM_BLOCKCHAIN
    end_height = start_height + NUM_BLOCKCHAIN
    return list(range(start_height, end_height))


def update_one_draw():
    current_draw_id = get_max_draw_id()
    current_bitcoin_height = get_max_bitcoin_height()

    if current_draw_id is None:
        current_draw_id = 0
    else:
        current_draw_id = current_draw_id + 1  # Increment to the next draw ID

    # Check if there are enough Bitcoin blocks to generate a new draw
    if current_bitcoin_height < (current_draw_id + 1) * NUM_BLOCKCHAIN:
        return False

    # Get the latest 144 Bitcoin block hashes
    heights = get_heights_by_draw_id(current_draw_id)
    bitcoins = select_bitcoin_by_height(heights)
    hashs = [bitcoin.hash for bitcoin in bitcoins]
    front, back = generate_lotto_numbers_bitcoin(hashs)

    # Update the database with the new draw
    create_draw([(current_draw_id, front, back, bitcoins[-1].timestamp, min(heights), max(heights))])
    return True

def update_draws():
    if IS_UPDATE_BITCOIN:
        update_bitcoins()
    while IS_UPDATE_DRAW and update_one_draw():
        logger.info(f"Updated draw {get_max_draw_id()} successfully.")

def update_statistics():
    """
    Update the statistics in the database.
    """
    if get_max_draw_id() is None:
        return False
    
    # Get all draws
    draws = get_all_draws()

    if not draws:
        return False

    front_all = [num for draw in draws for num in draw.front_list]
    back_all = [draw.back_int for draw in draws]
    # Plot number distribution charts and get stats
    front_stats, back_stats = plot_distribution(front_all, back_all, len(draws))

    # Update statistics in the database
    create_statistics([(len(draws), front_stats['chi2'], front_stats['p_value'], front_stats['conclusion'],
                        back_stats['chi2'], back_stats['p_value'], back_stats['conclusion'])])
    
    return True