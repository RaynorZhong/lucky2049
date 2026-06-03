import hashlib
import hmac
from typing import List, Tuple
from app.models import *
from app.bitcoin import update_bitcoins
from verify import commitment_for, GENESIS_PREV  # standalone auditor module at repo root

# numpy/scipy/plotly are only used by the statistics helpers below; they are
# imported lazily there so the draw engine and API hot paths start fast and
# don't pull ~700ms of heavy imports they never need.

# from bitcoinlib.services.services import Service

# Algorithm version. FROZEN. See SPEC.md.
# Any change to the generation logic or game parameters below REQUIRES bumping
# this version, and the new version applies to FUTURE draws only. Historical
# draws remain verifiable under the version they were generated with.
ALGO_VERSION = "v1"

NUM_BLOCKCHAIN = 144  # Number of blockchain hashes per group (Super Lotto / 大乐透)
BLUE_BALL_MAX = 35    # front zone range 1..35
RED_BALL_MAX = 12     # back zone range 1..12
BLUE_BALL_NUM = 5     # front: pick 5
RED_BALL_NUM = 2      # back: pick 2
IS_UPDATE_DRAW = True  # Whether to update the draw automatically
IS_UPDATE_BITCOIN = True  # Whether to update the Bitcoin blocks automatically

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

def generate_lotto_numbers_bitcoin(hashes: List[str]) -> Tuple[List[int], List[int]]:
    """
    Generate lottery numbers using 144 Bitcoin block hashes.
    
    Args:
        hashes: List of 144 SHA-256 block hashes (each hash is a 64-character hex string).
        # timestamp: Optional timestamp (string format) as extra entropy.
    
    Returns:
        Tuple[List[int], List[int]]: Front area numbers (5 sorted integers from 1 to BLUE_BALL_MAX), back area numbers (2 sorted integers from 1 to RED_BALL_MAX).
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
    random_numbers = deterministic_rng(seed, BLUE_BALL_NUM + RED_BALL_NUM)  # Need 7 random numbers (5 front + 2 back)

    # Step 5: Generate front area numbers (5 unique numbers from 1 to BLUE_BALL_MAX)
    front_pool = list(range(1, BLUE_BALL_MAX+1))
    front_numbers = []
    for i in range(BLUE_BALL_NUM):
        index = random_numbers[i] % len(front_pool)
        front_numbers.append(front_pool.pop(index))
    front_numbers.sort()
    
    # Step 6: Generate back area numbers (2 unique numbers from 1 to RED_BALL_MAX)
    back_pool = list(range(1, RED_BALL_MAX+1))
    back_numbers = []
    for i in range(RED_BALL_NUM):
        index = random_numbers[BLUE_BALL_NUM + i] % len(back_pool)
        back_numbers.append(back_pool.pop(index))
    back_numbers.sort()
    
    return front_numbers, back_numbers

def verify_lotto_numbers(hashes: List[str], front: List[int], back: List[int]) -> bool:
    """
    Verify if the lottery numbers are generated from the given block hashes.
    
    Args:
        hashes: 144 block hashes.
        front: Front area numbers.
        back: Back area numbers.
        # timestamp: Optional timestamp.
    
    Returns:
        bool: True if the numbers are generated from the hashes, False otherwise.
    """
    expected_front, expected_back = generate_lotto_numbers_bitcoin(hashes)
    return front == expected_front and back == expected_back


def get_spec() -> dict:
    """Machine-readable summary of the frozen algorithm spec (see SPEC.md)."""
    return {
        "algo_version": ALGO_VERSION,
        "status": "frozen",
        "game": "super_lotto",
        "front": {"pick": BLUE_BALL_NUM, "min": 1, "max": BLUE_BALL_MAX},
        "back": {"pick": RED_BALL_NUM, "min": 1, "max": RED_BALL_MAX},
        "blocks_per_draw": NUM_BLOCKCHAIN,
        "input_selection": "heights [draw_id*144, draw_id*144+143], genesis-anchored, deterministic",
        "seed": "SHA256(utf8(concat of 144 lowercase-hex block hashes, ascending height))",
        "rng": "HMAC_SHA256(key=seed, msg=ascii(str(counter))), big-endian uint256, counter=0..6",
        "mapping": "idx = int mod len(pool); pool.pop(idx); front then back; each sorted ascending",
        "spec_doc": "SPEC.md",
    }


def build_draw_manifest(draw_id: int) -> dict:
    """
    Build the per-draw public declaration (manifest) for a given draw_id.

    Contains everything needed to independently reproduce the result: the exact
    block heights, their 144 hashes (ascending), the derived seed, the published
    result, and a self-recomputation check. See SPEC.md and verify.py.
    """
    draw = get_draw_by_id(draw_id)
    if draw is None:
        return {"error": "Invalid draw number", "draw_id": draw_id}

    heights = get_heights_by_draw_id(draw_id)
    bitcoins = select_bitcoin_by_height(heights)
    hashes = [b.hash for b in bitcoins]

    published_front = draw.front_list
    published_back = draw.back_list

    prev_commitment = (
        GENESIS_PREV if draw_id == 0
        else getattr(get_draw_by_id(draw_id - 1), "commitment", None)
    )

    manifest = {
        "algo_version": getattr(draw, "algo_version", ALGO_VERSION),
        "spec": get_spec(),
        "draw_id": draw_id,
        "height_range": [min(heights), max(heights)],
        "num_blocks": len(hashes),
        "timestamp": draw.timestamp,
        "result": {"front": published_front, "back": published_back},
        "block_hashes": hashes,
        "commitment": getattr(draw, "commitment", None),
        "prev_commitment": prev_commitment,
        "verify": (
            "Independently fetch Bitcoin mainnet block hashes for heights "
            f"{min(heights)}..{max(heights)} (any full node / explorer), then follow SPEC.md "
            f"({getattr(draw, 'algo_version', ALGO_VERSION)}). Or run: python verify.py "
            f"{draw_id} --site <this site> to also check the tamper-evidence chain."
        ),
    }

    # Self-recomputation check (only meaningful when all 144 hashes are present).
    if len(hashes) == NUM_BLOCKCHAIN:
        seed = hashlib.sha256(''.join(hashes).encode('utf-8')).hexdigest()
        recomputed_front, recomputed_back = generate_lotto_numbers_bitcoin(hashes)
        manifest["seed_sha256"] = seed
        manifest["reproduced"] = (
            recomputed_front == published_front and recomputed_back == published_back
        )
    else:
        manifest["seed_sha256"] = None
        manifest["reproduced"] = None
        manifest["warning"] = (
            f"Only {len(hashes)}/{NUM_BLOCKCHAIN} block hashes available locally; "
            "fetch the full range from chain to verify."
        )

    return manifest


def backfill_commitments() -> int:
    """Fill the tamper-evidence commitment chain for any draws missing it.

    Walks draws in ascending id, recomputing each seed from stored hashes and
    chaining commitments from GENESIS_PREV. Idempotent: a draw whose commitment
    already matches its recomputation is left untouched; the walk still threads
    the (correct) prev through so later draws chain consistently. Returns the
    number of draws written. Run once after deploying the commitment feature.
    """
    written = 0
    prev = GENESIS_PREV
    with Session(engine) as session:
        draws = session.exec(select(Draw).order_by(Draw.id.asc())).all()
        for draw in draws:
            heights = get_heights_by_draw_id(draw.id)
            bitcoins = select_bitcoin_by_height(heights)
            hashes = [b.hash for b in bitcoins]
            if len(hashes) != NUM_BLOCKCHAIN:
                logger.warning(
                    "backfill: draw %s has %s/%s hashes; stopping chain here.",
                    draw.id, len(hashes), NUM_BLOCKCHAIN,
                )
                break
            seed_hex = hashlib.sha256(''.join(hashes).encode('utf-8')).hexdigest()
            expected = commitment_for(
                prev, draw.id, getattr(draw, "algo_version", ALGO_VERSION) or ALGO_VERSION,
                seed_hex, draw.front_list, draw.back_list, min(heights), max(heights),
            )
            if draw.commitment != expected:
                draw.commitment = expected
                session.add(draw)
                written += 1
            prev = expected
        if written:
            session.commit()
    logger.info("backfill_commitments: wrote %s commitment(s); head=%s", written, prev)
    return written


def get_commitment_head() -> dict:
    """The single value that commits to the entire published draw history.

    Anchor this externally (OpenTimestamps / a git tag / a public post) so the
    operator cannot rewrite history and recompute a new consistent head.
    """
    max_id = get_max_draw_id()
    if max_id is None:
        return {"head": GENESIS_PREV, "draw_id": None, "count": 0}
    head_draw = get_draw_by_id(max_id)
    return {
        "head": getattr(head_draw, "commitment", None),
        "draw_id": max_id,
        "count": max_id + 1,
        "algo_version": getattr(head_draw, "algo_version", ALGO_VERSION),
    }


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
    import numpy as np
    from scipy.stats import chi2_contingency

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
    import numpy as np
    import plotly.graph_objects as go

    # Front area chi-square test
    front_expected_freq = (total_draws * BLUE_BALL_NUM) / BLUE_BALL_MAX  # 5 numbers per draw, BLUE_BALL_MAX total numbers
    front_chi2, front_p = chi_square_test(front_all, BLUE_BALL_MAX, front_expected_freq)
    front_stats = {
        "chi2": round(front_chi2, 2),
        "p_value": round(front_p, 4),
        "conclusion": "Uniform distribution (good randomness)" if front_p > 0.05 else "Non-uniform distribution (possible bias)"
    }

    # Back area chi-square test
    back_expected_freq = (total_draws * RED_BALL_NUM) / RED_BALL_MAX  # 2 numbers per draw, RED_BALL_MAX total numbers
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

    # Only draw once the full 144-block window is present in the store. Stored
    # blocks are ingested only after CONFIRMATIONS confirmations (see
    # bitcoin.update_bitcoins), so a complete window here is already deeply
    # confirmed and robust to shallow reorgs.
    if current_bitcoin_height < (current_draw_id + 1) * NUM_BLOCKCHAIN:
        return False

    # Get the latest 144 Bitcoin block hashes
    heights = get_heights_by_draw_id(current_draw_id)
    bitcoins = select_bitcoin_by_height(heights)
    hashs = [bitcoin.hash for bitcoin in bitcoins]
    front, back = generate_lotto_numbers_bitcoin(hashs)

    # Tamper-evidence: chain this draw onto the previous one's commitment.
    seed_hex = hashlib.sha256(''.join(hashs).encode('utf-8')).hexdigest()
    if current_draw_id == 0:
        prev = GENESIS_PREV
    else:
        prev = getattr(get_draw_by_id(current_draw_id - 1), "commitment", None)
    if prev is None:
        # Chain not established yet (e.g. backfill_commitments() not run). Store
        # the draw now without breaking, and leave the commitment for backfill.
        logger.warning(
            "Draw %s: previous commitment missing; storing without a commitment. "
            "Run backfill_commitments() to (re)build the chain.", current_draw_id,
        )
        commitment = None
    else:
        commitment = commitment_for(
            prev, current_draw_id, ALGO_VERSION, seed_hex, front, back, min(heights), max(heights)
        )

    # Update the database with the new draw (stamped with algo version + commitment)
    create_draw([(current_draw_id, front, back, bitcoins[-1].timestamp, min(heights), max(heights), ALGO_VERSION, commitment)])
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
    back_all = [num for draw in draws for num in draw.back_list]
    # Plot number distribution charts and get stats
    front_stats, back_stats = plot_distribution(front_all, back_all, len(draws))

    # Update statistics in the database
    create_statistics([(len(draws), front_stats['chi2'], front_stats['p_value'], front_stats['conclusion'],
                        back_stats['chi2'], back_stats['p_value'], back_stats['conclusion'])])
    
    return True