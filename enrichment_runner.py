#!/usr/bin/env python3
# deps: requests

"""
Enrichment Runner Daemon

Continuously polls mcp_server_registry for MCPs not yet enriched,
applies all available enrichment modules, and writes results to
mcp_signal_enrichments table via write_service.
"""

import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_HEALTH_URL = "http://127.0.0.1:8772/health"
HEARTBEAT_INTERVAL = 60  # seconds
POLL_INTERVAL = 5  # seconds between poll cycles
BATCH_SIZE = 50
REQUEST_TIMEOUT = 10
WRITE_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_FACTOR = 2

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _retry_with_backoff(func, *args, **kwargs):
    """Execute func with exponential backoff on failure."""
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait_time = BACKOFF_FACTOR ** attempt
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logger.error(f"All {MAX_RETRIES} attempts failed: {e}")
    raise last_exception


def send_heartbeat():
    """Send heartbeat to service_health."""
    try:
        response = requests.post(
            SERVICE_HEALTH_URL,
            json={"service": "enrichment_runner", "status": "running", "timestamp": datetime.now(timezone.utc).isoformat()},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.debug("Heartbeat sent successfully")
    except Exception as e:
        logger.warning(f"Failed to send heartbeat: {e}")


def fetch_pending_mcps() -> list:
    """Fetch MCPs not yet enriched from registry via write_service."""
    query = """
        SELECT mcp_name 
        FROM mcp_server_registry 
        WHERE mcp_name NOT IN (SELECT DISTINCT mcp_name FROM mcp_signal_enrichments)
        LIMIT {limit}
    """.format(limit=BATCH_SIZE)
    
    def _do_query():
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": query},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    
    try:
        result = _retry_with_backoff(_do_query)
        rows = result.get('rows', [])
        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Failed to fetch pending MCPs: {e}")
        return []


def get_mcp_metadata(mcp_name: str) -> dict:
    """Fetch metadata for a specific MCP from registry."""
    query = f"SELECT metadata FROM mcp_server_registry WHERE mcp_name = '{mcp_name}'"
    
    def _do_query():
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": query},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    
    try:
        result = _retry_with_backoff(_do_query)
        rows = result.get('rows', [])
        if rows and rows[0]:
            metadata_str = rows[0][0]
            if metadata_str:
                return json.loads(metadata_str)
    except Exception as e:
        logger.warning(f"Failed to fetch metadata for {mcp_name}: {e}")
    
    return {}


def compute_enrichment_scores(metadata: dict) -> list:
    """Compute scores from all available enrichment modules."""
    scores = []
    
    # Compute supply chain enrichment
    try:
        from supply_chain_enrichment import compute_score
        score, evidence = compute_score(metadata)
        scores.append(('supply_chain', score, evidence))
    except ImportError:
        logger.warning("supply_chain_enrichment not available")
    except Exception as e:
        logger.warning(f"supply_chain_enrichment.compute_score failed: {e}")
    
    # Compute community signal enrichment
    try:
        from community_signal_enrichment import compute_score
        score, evidence = compute_score(metadata)
        scores.append(('community_signal', score, evidence))
    except ImportError:
        logger.warning("community_signal_enrichment not available")
    except Exception as e:
        logger.warning(f"community_signal_enrichment.compute_score failed: {e}")
    
    # Compute supply chain wiring enrichment
    try:
        from supply_chain_enrichment_wiring import compute_score
        score, evidence = compute_score(metadata)
        scores.append(('supply_chain_wiring', score, evidence))
    except ImportError:
        logger.warning("supply_chain_enrichment_wiring not available")
    except Exception as e:
        logger.warning(f"supply_chain_enrichment_wiring.compute_score failed: {e}")
    
    return scores


def write_enrichment(mcp_name: str, signal_type: str, score: float, evidence_blob: dict):
    """Write enrichment result to mcp_signal_enrichments via write_service."""
    payload = {
        "mcp_name": mcp_name,
        "signal_type": signal_type,
        "score": score,
        "evidence_blob": json.dumps(evidence_blob),
        "computed_at": datetime.now(timezone.utc).isoformat()
    }
    
    def _do_write():
        response = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=WRITE_TIMEOUT
        )
        response.raise_for_status()
        return response
    
    try:
        _retry_with_backoff(_do_write)
        logger.info(f"Wrote enrichment for {mcp_name} ({signal_type})")
    except Exception as e:
        logger.error(f"Failed to write enrichment for {mcp_name}: {e}")


def process_mcp(mcp_name: str):
    """Process a single MCP through all enrichment modules."""
    metadata = get_mcp_metadata(mcp_name)
    if not metadata:
        logger.warning(f"No metadata for {mcp_name}, skipping")
        return
    
    scores = compute_enrichment_scores(metadata)
    
    for signal_type, score, evidence in scores:
        write_enrichment(mcp_name, signal_type, score, evidence)


def run():
    """Main daemon loop."""
    logger.info("Starting enrichment runner daemon")
    last_heartbeat = time.time()
    
    while True:
        try:
            pending_mcps = fetch_pending_mcps()
            if pending_mcps:
                logger.info(f"Processing {len(pending_mcps)} pending MCPs")
                for mcp_name in pending_mcps:
                    process_mcp(mcp_name)
            else:
                logger.debug("No pending MCPs found")
        except Exception as e:
            logger.error(f"Error in work cycle: {e}")
        
        # Send heartbeat if needed
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat()
            last_heartbeat = time.time()
        
        time.sleep(POLL_INTERVAL)


def _daemon_startup_test():
    """Test that run() enters loop without blocking within 10s."""
    loop_entered = threading.Event()
    
    def run_with_flag():
        """Wrapper that signals when loop iteration starts."""
        original_sleep = time.sleep
        def interruptible_sleep(duration):
            if duration <= 1:
                original_sleep(duration)
            else:
                loop_entered.set()
                original_sleep(0.01)
        time.sleep = interruptible_sleep
        try:
            run()
        finally:
            time.sleep = original_sleep
    
    thread = threading.Thread(target=run_with_flag, daemon=True)
    thread.start()
    
    if loop_entered.wait(timeout=10):
        print("PASS: Daemon entered loop within 10s")
        return True
    else:
        print("FAIL: Daemon did not enter loop within 10s")
        return False


def _self_test():
    """Self-test for enrichment modules."""
    print("Running self-test...")
    
    # Synthetic metadata for testing
    metadata = {
        'name': 'test-mcp',
        'version': '1.0.0',
        'description': 'Test MCP for validation',
        'dependencies': ['dep1', 'dep2'],
        'author': 'test-author',
        'repository': 'https://github.com/test/test-mcp',
        'download_count': 1000,
        'stars': 50,
        'forks': 10,
        'open_issues': 5,
        'license': 'MIT',
        'verified': True,
        'security_audit': 'passed',
        'maintainer_activity': 'active',
    }
    
    # Test supply_chain_enrichment
    try:
        from supply_chain_enrichment import compute_score
        score, evidence = compute_score(metadata)
        assert 0 <= score <= 100, f"Score out of range: {score}"
        assert 'verdict' in evidence, f"Missing 'verdict' in evidence: {evidence}"
        print(f"  supply_chain_enrichment: PASS (score={score})")
    except ImportError:
        print("  supply_chain_enrichment: SKIP (not available)")
    except Exception as e:
        print(f"  supply_chain_enrichment: FAIL ({e})")
        raise
    
    # Test community_signal_enrichment
    try:
        from community_signal_enrichment import compute_score
        score, evidence = compute_score(metadata)
        assert 0 <= score <= 100, f"Score out of range: {score}"
        assert 'verdict' in evidence, f"Missing 'verdict' in evidence: {evidence}"
        print(f"  community_signal_enrichment: PASS (score={score})")
    except ImportError:
        print("  community_signal_enrichment: SKIP (not available)")
    except Exception as e:
        print(f"  community_signal_enrichment: FAIL ({e})")
        raise
    
    print("PASS")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--daemon-test':
        # Daemon startup test mode
        sys.exit(0 if _daemon_startup_test() else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == '--self-test':
        # Self-test mode
        _self_test()
    else:
        # Run daemon normally
        run()