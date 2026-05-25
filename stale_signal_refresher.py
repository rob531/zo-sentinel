#!/usr/bin/env python3
"""
Stale Signal Refresher Daemon
Refreshes signal scores older than 7 days by calling enrichment modules.
"""

import sys
import os
import time
import signal
import threading
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

# Database access via HTTP API
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
WRITE_API_URL = f"{WRITE_SERVICE_URL}/write"
QUERY_API_URL = f"{WRITE_SERVICE_URL}/query"

SERVICE_NAME = "stale_signal_refresher"
LOCK_FILE = f"/tmp/{SERVICE_NAME}.pid"
POLL_SECS = 3600
STALE_THRESHOLD_DAYS = 7
MAX_ENRICHMENT_SECS = 2.0
MAX_STALE_RECORDS = 100

ENRICHMENT_MODULE_PATH = "/home/workspace/zo_sentinel"


def check_single_instance():
    """Ensure only one instance runs."""
    pid = os.getpid()
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE) as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            print(f"Another instance (PID {existing_pid}) is already running. Exiting.")
            sys.exit(1)
        except OSError:
            pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(pid))


def send_heartbeat():
    """Send heartbeat to service health registry."""
    try:
        requests.post(WRITE_API_URL, json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=10)
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def ws_query(sql: str) -> Dict[str, Any]:
    """Execute a SELECT query via write_service."""
    try:
        response = requests.post(QUERY_API_URL, json={"sql": sql}, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Query failed: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write data via write_service."""
    try:
        response = requests.post(WRITE_API_URL, json={
            "table": table,
            "rows": rows,
            "wait": True
        }, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Write failed: {e}")
        return False


def get_stale_signals() -> list:
    """Query for stale signals (older than 7 days)."""
    sql = f"""
        SELECT server_id, signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE scored_at < now() - INTERVAL '7 days'
        ORDER BY scored_at ASC
        LIMIT {MAX_STALE_RECORDS}
    """
    result = ws_query(sql)
    return result.get("rows", [])


def get_registry_server(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch registry metadata for a server."""
    safe_id = server_id.replace("'", "''")
    sql = f"SELECT * FROM mcp_server_registry WHERE server_id = '{safe_id}'"
    result = ws_query(sql)
    rows = result.get("rows", [])
    return rows[0] if rows else None


def load_enrichment_module(signal_name: str):
    """Dynamically load the enrichment module for a signal."""
    module_name = f"{signal_name}_enrichment"
    module_path = os.path.join(ENRICHMENT_MODULE_PATH, f"{module_name}.py")
    
    if not os.path.exists(module_path):
        return None
    
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


class TimeoutException(Exception):
    pass


def timed_compute_score(enrichment_module, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper to run compute_score with alarm-based timeout.
    Uses signal.SIGALRM on the main thread - only works outside threads.
    For thread-safety, we use threading.Event polling in a separate approach.
    """
    result_holder = [None]
    error_holder = [None]
    done_event = threading.Event()
    
    def worker():
        try:
            if hasattr(enrichment_module, 'compute_score'):
                result_holder[0] = enrichment_module.compute_score(context)
            else:
                error_holder[0] = "Module missing compute_score function"
        except Exception as e:
            error_holder[0] = str(e)
        finally:
            done_event.set()
    
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    
    # Wait with timeout using polling
    start = time.time()
    while not done_event.is_set():
        if time.time() - start > MAX_ENRICHMENT_SECS:
            # Thread will continue but result will be ignored
            raise TimeoutException(f"compute_score exceeded {MAX_ENRICHMENT_SECS}s timeout")
        time.sleep(0.05)  # Poll every 50ms
    
    worker_thread.join(max(0.1, MAX_ENRICHMENT_SECS - (time.time() - start)))
    
    if error_holder[0]:
        raise Exception(error_holder[0])
    
    return result_holder[0]


def process_stale_signal(server_id: str, signal_name: str, old_score: Any, old_evidence: Any) -> bool:
    """Process a single stale signal - fetch metadata, compute new score, write back."""
    
    # Get current registry metadata
    registry_data = get_registry_server(server_id)
    if not registry_data:
        print(f"Server {server_id} not in registry, skipping signal {signal_name}")
        return False

    # Load the enrichment module
    enrichment_module = load_enrichment_module(signal_name)
    if not enrichment_module:
        print(f"No enrichment module for signal {signal_name}, skipping")
        return False

    # Prepare context for compute_score
    context = {
        'server_id': server_id,
        'registry_data': registry_data,
        'old_score': old_score,
        'old_evidence': old_evidence
    }

    # Run compute_score with timeout
    new_data = None
    try:
        new_data = timed_compute_score(enrichment_module, context)
    except TimeoutException as e:
        print(f"Signal {signal_name} compute_score timeout: {e}")
        return False
    except Exception as e:
        print(f"Signal {signal_name} compute_score failed: {e}")
        return False

    if new_data is None:
        print(f"Signal {signal_name} compute_score returned None")
        return False

    # Write new score to mcp_signal_enrichments
    now = datetime.utcnow().isoformat()
    new_rows = {
        'server_id': server_id,
        'signal_name': signal_name,
        'score': new_data.get('score', old_score),
        'evidence': new_data.get('evidence', old_evidence),
        'computed_at': now
    }

    write_ok = ws_write('mcp_signal_enrichments', new_rows)
    if write_ok:
        print(f"Refreshed {signal_name} for server {server_id}")
    else:
        print(f"Failed to write enrichment for {signal_name}/{server_id}")

    return write_ok


def process_cycle() -> Tuple[int, int]:
    """Process one cycle of stale signals."""
    print(f"[{datetime.utcnow().isoformat()}] Starting stale signal refresh cycle")

    stale_signals = get_stale_signals()
    print(f"Found {len(stale_signals)} stale signals")

    success_count = 0
    skip_count = 0

    for signal in stale_signals:
        server_id = signal.get('server_id')
        signal_name = signal.get('signal_name')
        old_score = signal.get('score')
        old_evidence = signal.get('evidence')

        if not server_id or not signal_name:
            print(f"Invalid signal record, skipping: {signal}")
            skip_count += 1
            continue

        ok = process_stale_signal(server_id, signal_name, old_score, old_evidence)
        if ok:
            success_count += 1
        else:
            skip_count += 1

    print(f"Cycle complete: {success_count} refreshed, {skip_count} skipped")
    return success_count, skip_count


def run():
    """Main daemon loop."""
    print(f"Starting {SERVICE_NAME} daemon...")
    check_single_instance()
    
    start_time = time.time()

    try:
        while True:
            cycle_start = time.time()

            try:
                process_cycle()
            except Exception as e:
                print(f"Cycle error: {e}")

            send_heartbeat()

            # Maintain ~3600s interval
            elapsed = time.time() - cycle_start
            sleep_time = max(1, POLL_SECS - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == '__main__':
    run()