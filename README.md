# lucky2049 - Bitcoin Block Hash Lottery Generator

## Overview

**lucky2049** is a Python-based project designed to generate lottery numbers for games like Powerball using verifiable randomness from Bitcoin blockchain hashes. It leverages 144 consecutive Bitcoin block hashes to create a deterministic seed, ensuring transparency and reproducibility. The generated numbers are:

- White balls: 5 unique numbers from 1 to 69 (configurable).
- Powerball: 1 number from 1 to 26 (configurable).

This tool is ideal for simulations, educational purposes, or exploring cryptographic randomness in lotteries. The process uses HMAC-SHA256 for pseudo-random number generation, making it auditable—anyone with the same hashes can verify the output.

**Repository:** [https://github.com/RaynorZhong/lucky2049](https://github.com/RaynorZhong/lucky2049)

**Note:** This is for entertainment only. Gambling laws apply; do not use for real betting. Always verify official Powerball results.

## Features

- **Verifiable Randomness:** Sources entropy from public Bitcoin blocks, which are immutable and globally verifiable.
- **Deterministic RNG:** HMAC-SHA256 ensures consistent results for the same inputs.
- **Configurable Lottery Rules:** Easily switch between Powerball (69/26) and other formats like Super Lotto (35/12) via global constants.
- **Modular Design:** Separate functions for RNG and number generation.
- **Optional Data Sources:** Supports loading hashes from CSV files (via commented `getBitcoinBlockList` function using pandas).
- **Automation Flags:** `IS_UPDATE_DRAW` and `IS_UPDATE_BITCOIN` for potential scripted updates (e.g., cron jobs).

## Requirements

- Python 3.6+
- Built-in: `hmac`, `hashlib`
- Typing: `from typing import List, Tuple` (for hints)
- Optional:
  - `pandas` (for CSV hash loading: `pip install pandas`)

## Installation

1. Clone the repository:

   ```shell
   git clone https://github.com/RaynorZhong/lucky2049.git
   cd lucky2049
   ```

2. Install optional dependencies (if using CSV or Bitcoin fetching):

   ```shell
   pip install -r requirements.txt 
   ```

## Usage

The core functionality is in `generate_lotto_numbers_bitcoin(hashes)`, which takes a list of 144 Bitcoin hashes.

### Step 1: Obtain Bitcoin Hashes

- **From CSV:** Uncomment and use `getBitcoinBlockList(filename)` (expects `./input/{filename}.csv` with 'hash' and 'timestamp' columns).

- **Manually:** Collect from explorers like [mempool.space](https://mempool.space).

### Step 2: Generate Numbers

```python
# In your script (e.g., main.py)
from typing import List, Tuple  # If using type hints
# Import your functions and globals here

hashes_list: List[str] = [...]  # Your 144 hashes
front, back = generate_lotto_numbers_bitcoin(hashes_list)
print(f"White Balls: {' '.join(map(str, front))}")
print(f"Powerball: {back}")
```

### Example Output

```python
White Balls: 3 12 18 25 34
Powerball: 5
```

### Adding Extra Entropy

Uncomment the timestamp lines in `generate_lotto_numbers_bitcoin` and provide a string timestamp (e.g., from CSV or `datetime.now().isoformat()`) for additional variability.

## Configuration

- Edit globals in the script:
  
  ```python
  NUM_BLOCKCHAIN = 144  # Hashes to use
  BLUE_BALL_MAX = 69    # White balls max
  RED_BALL_MAX = 26     # Powerball max
  IS_UPDATE_DRAW = True # Flag for auto-updates
  IS_UPDATE_BITCOIN = True
  ```

- For Super Lotto: Set `BLUE_BALL_MAX=35`, `RED_BALL_MAX=12`, and modify back drawing to select 2 unique numbers.

## Code Structure

- **Globals:** Define lottery parameters and update flags.
- **Deterministic RNG** deterministic_rng(seed: bytes, count: int) -> List[int] – HMAC-SHA256 generator.
- **Generate Lotto Numbers Bitcoin** generate_lotto_numbers_bitcoin(hashes: List[str]) -> Tuple[List[int], int] – Main function: Validates input, creates seed, generates and sorts numbers.
- **Commented Functions:** `getBitcoinBlockList(filename)` for CSV input (returns hashes and timestamps).

## How It Works

(1) Concatenate 144 hex hashes into a string (optionally + timestamp).
(2) SHA-256 hash to derive a 32-byte seed.
(3) Generate 6 large random numbers using HMAC with counter.
(4) Draw white balls by indexing and popping from a pool (ensures uniqueness).
(5) Draw Powerball via modulo operation.
(6) Sort and return.

This method minimizes bias and ensures cryptographic strength.

## Limitations

- **No Built-in Fetching:** Hashes must be supplied; extend with bitcoinlib for automation.
- **Bias** Negligible due to large RNG range (2^256).
- **Dependencies:** Internet for fetching; no pip installs in isolated envs.
- **Scalability:** Fixed to 6 random values; for more, adjust `count`.
- **Blockchain Delays:** Bitcoin blocks produce every ~10 minutes; use recent data.

## Contributing

Contributions welcome! Fork the repo, create PRs for features like GUI, multi-lottery support, or improved fetching.

## License

MIT License – See [LICENSE](LICENSE) for details.

For issues or suggestions, open a GitHub issue. Project by RaynorZhong.