from tenacity import retry, stop_after_attempt, wait_exponential
from typing import List, Dict
from datetime import datetime
from dateutil import parser
from db.models import *
import requests
import logging
import pytz
import json

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addHandler(DatabaseHandler(level=logging.INFO))

# Utility functions
def parse_timestamp(timestamp_input) -> str:
    if isinstance(timestamp_input, (int, float, str)) and str(timestamp_input).isdigit():
        return datetime.fromtimestamp(float(timestamp_input), tz=pytz.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        parsed = parser.parse(timestamp_input, ignoretz=False)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=pytz.UTC)
        return parsed.astimezone(pytz.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        raise ValueError(f"Cannot parse timestamp: {timestamp_input}")

# Blockchair V2 API
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=60, min=120, max=480))
def fetch_height_by_blockchair_v2() -> int:
    HEIGHT_URL = "https://api.blockchair.com/bitcoin/stats"
    try:
        response = requests.get(HEIGHT_URL)
        response.raise_for_status()
        data = response.json()
        return data["data"]["blocks"] - 1  # Latest block height
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to get latest block height: {str(e)}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=60, min=120, max=480))
def fetch_bitcoins_by_blockchair_v2(start_height: int, count: int) -> List[Dict[str, str]]:
    BITCOINS_URL = "https://api.blockchair.com/bitcoin/blocks"
    hashes = []

    try:
        params = {
            "limit": count,
            "s": "id(asc)", 
            "q": f"id({start_height}..)"
        }
        try:
            response = requests.get(BITCOINS_URL, params=params)
            response.raise_for_status()
            data = response.json()["data"]
            if not data:
                raise ValueError(f"No data retrieved")

            # Extract hash and height
            for block in data:
                block_hash = block["hash"]
                block_height = block["id"]
                block_time = parse_timestamp(block["time"])
                if len(block_hash) != 64 or not all(c in "0123456789abcdef" for c in block_hash):
                    raise ValueError(f"Invalid block hash: {block_hash}")
                # Only add blocks with height >= start_height
                if block_height >= start_height:
                    hashes.append({"height": block_height, "hash": block_hash, "timestamp": block_time})

            # Check if the number of hashes matches the requested count
            if len(hashes) != count:
                raise ValueError(f"Number of block hashes retrieved {len(hashes)} does not match requested count {count}")

        except requests.exceptions.HTTPError as e:
            error_data = response.json() if response.content else {}
            raise ValueError(f"API request failed: {e.response.status_code} - {json.dumps(error_data)}")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Network connection failed: {str(e)}")

        return sorted(hashes, key=lambda x: x["height"])

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise


# BlockCypher API - always limits reached.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_height_by_blockcypher() -> int:
    HEIGHT_URL = "https://api.blockcypher.com/v1/btc/main"
    try:
        response = requests.get(HEIGHT_URL)
        response.raise_for_status()
        data = response.json()
        return data["height"]
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to get latest block height: {str(e)}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_block_by_blockcypher(height: int) -> Dict[str, str]:
    BLOCK_URL = f"https://api.blockcypher.com/v1/btc/main/blocks/{height}"
    try:
        response = requests.get(BLOCK_URL)
        response.raise_for_status()
        data = response.json()
        
        block =  {
            "height": data["height"],
            "hash": data["hash"],
            "timestamp": parse_timestamp(data["time"])
        }

        if len(block["hash"]) != 64 or not all(c in "0123456789abcdef" for c in block["hash"]):
            raise ValueError(f"Invalid block hash: {block['hash']}")
        
        return block
    
    except requests.exceptions.HTTPError as e:
        error_data = response.json() if response.content else {}
        raise ValueError(f"API request failed: {e.response.status_code} - {json.dumps(error_data)}")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to get block {height}: {str(e)}")

def fetch_bitcoins_by_blockcypher(start_height: int, count: int) -> List[Dict[str, str]]:
    hashes = []

    try:
        for i in range(start_height, start_height + count):
            block = fetch_block_by_blockcypher(i)
            if block["height"] >= start_height:
                hashes.append(block)
        # Check if the number of hashes matches the requested count
        if len(hashes) != count:
            raise ValueError(f"Number of block hashes retrieved {len(hashes)} does not match requested count {count}")

        return sorted(hashes, key=lambda x: x["height"])

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise


# Mempool.space API
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_height_by_mempool_space() -> int:
    HEIGHT_URL = "https://mempool.space/api/blocks/tip/height"
    try:
        response = requests.get(HEIGHT_URL)
        response.raise_for_status()
        return int(response.text)
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Failed to get latest block height: {str(e)}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_blocks_by_mempool_space(height: int) -> List[Dict[str, str]]:
    BLOCK_URL = f"https://mempool.space/api/v1/blocks/{height}"
    blocks = []

    try:
        response = requests.get(BLOCK_URL)
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError(f"No data retrieved")

        # Extract hash and height
        for block in data:
            block_hash = block["id"]
            block_height = block["height"]
            block_time = parse_timestamp(block["timestamp"])
            if len(block_hash) != 64 or not all(c in "0123456789abcdef" for c in block_hash):
                raise ValueError(f"Invalid block hash: {block_hash}")
            
            blocks.append({"height": block_height, "hash": block_hash, "timestamp": block_time})

    except requests.exceptions.HTTPError as e:
        error_data = response.json() if response.content else {}
        raise ValueError(f"API request failed: {e.response.status_code} - {json.dumps(error_data)}")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network connection failed: {str(e)}")

    return blocks
    

def fetch_bitcoins_by_mempool_space(start_height: int, count: int) -> List[Dict[str, str]]:
    DEFAULT_BLOCKS_STEP = 14
    list_height = list(range(start_height, start_height + count))
    hashes = []

    try:
        for i in range(start_height, start_height + count, DEFAULT_BLOCKS_STEP):
            blocks = fetch_blocks_by_mempool_space(i + DEFAULT_BLOCKS_STEP)
            for block in blocks:
                if block["height"] in list_height:
                    hashes.append(block)
                    list_height.remove(block["height"])

        # Check if the number of hashes matches the requested count
        if len(hashes) != count:
            raise ValueError(f"Number of block hashes retrieved {len(hashes)} does not match requested count {count}")

        return sorted(hashes, key=lambda x: x["height"])

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise

def fetch_height() -> int:
    height = min(
        # fetch_height_by_blockcypher(),
        fetch_height_by_mempool_space(),
        fetch_height_by_blockchair_v2()
    )
    if height is None:
        raise ValueError("Failed to fetch the latest block height from all sources")
    return height

def fetch_bitcoins(start_height: int, count: int) -> List[Dict[str, str]]:
    list_bitcoins = [
        fetch_bitcoins_by_blockchair_v2(start_height, count),
        # fetch_bitcoins_by_blockcypher(start_height, count),
        fetch_bitcoins_by_mempool_space(start_height, count)
    ]
    ret = list_bitcoins[0]
    for list_bitcoin in list_bitcoins[1:]:
        if list_bitcoin == ret:
            continue
        else:
            logger.warning(f"Block hashes from different sources do not match, using the first source's data, {start_height}, {count}")
            break

    return ret

def validate_hashes(hashes: List[Dict[str, str]]) -> bool:
    heights = [block["height"] for block in hashes]
    if sorted(heights) != list(range(min(heights), max(heights) + 1)):
        raise ValueError("Block heights are not continuous")

    for block in hashes:
        if len(block["hash"]) != 64 or not all(c in "0123456789abcdef" for c in block["hash"]):
            raise ValueError(f"Invalid block hash: {block['hash']}")
    
    logger.info("All block hashes validated successfully")
    return True

def update_bitcoins():
    MAX_BLOCKCHAINS = 100
    try:
        start_height = get_max_bitcoin_height()
        start_height = start_height + 1 if start_height else 0
        latest_height = fetch_height()

        while start_height <= latest_height:
            count = min(MAX_BLOCKCHAINS, latest_height - start_height + 1)
            logger.info(f"Latest block height: {latest_height}, fetching {count} block hashes starting from height {start_height}")
            hashes = fetch_bitcoins(start_height, count)
            # Validate hashes
            validate_hashes(hashes)

            if add_bitcoin([(block["height"], block["hash"], block["timestamp"]) for block in hashes]):
                logger.info(f"Successfully updated {count} Bitcoin block hashes starting from height {start_height}")
                start_height += count
            else:
                logger.warning(f"Failed to update Bitcoin block hashes starting from height {start_height}, possibly due to non-continuous heights")
                break

        if start_height > latest_height:
            logger.info("All block hashes have been successfully updated")

    except Exception as e:
        logger.error(f"Execution failed: {str(e)}")