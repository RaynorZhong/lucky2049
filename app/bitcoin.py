from tenacity import retry, stop_after_attempt, wait_exponential
from typing import List, Dict
from datetime import datetime
from dateutil import parser
from app.models import *
import requests
import logging
import pytz
import json
import os

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
if os.environ.get("LOTTO_DISABLE_DB_LOG") != "1":
    logger.addHandler(DatabaseHandler(level=logging.WARNING))

# Reorg safety: only ingest blocks that are already buried by this many
# confirmations. Ingestion is append-only (we never re-fetch a stored block),
# so taking blocks straight from the chain tip would let a later reorg leave a
# stale hash in the DB forever. Lagging the tip by CONFIRMATIONS means every
# stored (and therefore every drawn) block was already this deep when captured,
# making draws robust to reorgs shallower than CONFIRMATIONS. This is purely an
# ingestion/finalization timing parameter — it does NOT change the v1 result for
# any given height (see SPEC.md). Tune up via env for higher-value deployments.
CONFIRMATIONS = int(os.environ.get("DRAW_CONFIRMATIONS", "6"))


def confirmed_tip(latest_height: int) -> int:
    """Highest height considered final: chain tip minus CONFIRMATIONS.

    May be negative when the chain is shorter than the confirmation depth
    (nothing is final yet); callers compare against a non-negative start height
    and simply skip ingestion in that case.
    """
    return latest_height - CONFIRMATIONS

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

# =========================================================================
# Bitcoin Core full node (JSON-RPC) — PRIMARY, canonical source of truth.
#
# Configure via environment:
#   BITCOIN_RPC_URL="http://user:pass@127.0.0.1:8332"     (preferred), or
#   BITCOIN_RPC_USER / BITCOIN_RPC_PASSWORD / BITCOIN_RPC_HOST / BITCOIN_RPC_PORT
#
# When a node is configured it is used as the source of truth; the public
# explorer above (mempool.space) remains as a fallback only (e.g. node
# temporarily unreachable). Block hashes are objective Bitcoin consensus data,
# so the node is canonical.
# =========================================================================
def core_rpc_url() -> str:
    url = os.environ.get("BITCOIN_RPC_URL")
    if url:
        return url
    user = os.environ.get("BITCOIN_RPC_USER")
    if not user:
        return ""
    pw = os.environ.get("BITCOIN_RPC_PASSWORD", "")
    host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
    port = os.environ.get("BITCOIN_RPC_PORT", "8332")
    return f"http://{user}:{pw}@{host}:{port}"

def core_enabled() -> bool:
    return bool(core_rpc_url())

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _core_call(method: str, params: list):
    url = core_rpc_url()
    if not url:
        raise RuntimeError("Bitcoin Core RPC is not configured")
    payload = {"jsonrpc": "1.0", "id": "lucky2049", "method": method, "params": params}
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise ValueError(f"RPC error for {method}: {data['error']}")
    return data["result"]

def fetch_height_by_core() -> int:
    return int(_core_call("getblockcount", []))

def fetch_bitcoins_by_core(start_height: int, count: int) -> List[Dict[str, str]]:
    hashes = []
    for height in range(start_height, start_height + count):
        block_hash = _core_call("getblockhash", [height])
        header = _core_call("getblockheader", [block_hash])
        block_hash = block_hash.strip().lower()
        if len(block_hash) != 64 or not all(c in "0123456789abcdef" for c in block_hash):
            raise ValueError(f"Invalid block hash from node: {block_hash}")
        hashes.append({
            "height": header["height"],
            "hash": block_hash,
            "timestamp": parse_timestamp(header["time"]),
        })
    return sorted(hashes, key=lambda x: x["height"])


def fetch_height() -> int:
    if core_enabled():
        try:
            height = fetch_height_by_core()
            if height is not None:
                return height
        except Exception as e:
            logger.warning(f"Core node height fetch failed, falling back to explorer: {e}")
    height = fetch_height_by_mempool_space()
    if height is None:
        raise ValueError("Failed to fetch the latest block height from all sources")
    return height

def fetch_bitcoins(start_height: int, count: int) -> List[Dict[str, str]]:
    # Primary: own full node (canonical). Fallback: public explorer.
    if core_enabled():
        try:
            return fetch_bitcoins_by_core(start_height, count)
        except Exception as e:
            logger.warning(f"Core node block fetch failed, falling back to explorer: {e}")
    list_bitcoins = [
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
        # None → fresh DB, start at genesis (0). Otherwise next height. (Using
        # `if start_height` would wrongly re-fetch from 0 when only block 0 exists.)
        start_height = 0 if start_height is None else start_height + 1
        # Only ingest up to the confirmed tip, never the raw chain tip, so a
        # later shallow reorg can't strand a stale hash in our append-only store.
        latest_height = confirmed_tip(fetch_height())
        if latest_height < start_height:
            logger.info(f"No new confirmed blocks (need {CONFIRMATIONS} confirmations); up to date at height {start_height - 1}")
            return

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