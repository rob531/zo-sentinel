#!/usr/bin/env python3
import sys
import os
import time
import signal
import json
import requests
from datetime import datetime, timezone

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = 'fingerprint_runner_daemon'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
HEARTBEAT_INTERVAL = 30
POLL_SECS = 600
BATCH_LIMIT = 50
LOCK_FILE = '/home/workspace/logs/fingerprint_runner_daemon.lock'
LOG_FILE = '/home/workspace/logs/fingerprint_runner_daemon.log'

started_at = time.time()

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_SERVICE_URL

def ws_query(sql):
    url = get_query_url()
    resp = requests.post(url, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(payload):
    url = get_write_url()
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat():
    url = get_write_url()
    rows = {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()}
    try:
        requests.post(url, json={'table': 'service_health', 'rows': rows}, timeout=10)
    except Exception as e:
        log(f"Heartbeat error: {e}")

def check_single_instance():
    lock_dir = os.path.dirname(LOCK_FILE)
    os.makedirs(lock_dir, exist_ok=True)
    import fcntl
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return True
    except IOError:
        existing_pid = None
        try:
            with open(LOCK_FILE, 'r') as lf:
                existing_pid = lf.read().strip()
        except Exception:
            pass
        if existing_pid:
            try:
                os.kill(int(existing_pid), 0)
                log(f"ERROR: Another instance already running (PID {existing_pid}). Exiting.")
                return False
            except OSError:
                log(f"Stale lockfile from PID {existing_pid}, removing and retrying...")
                try:
                    os.remove(LOCK_FILE)
                except Exception:
                    pass
                return check_single_instance()
        log("Could not acquire lock, assuming another instance is running.")
        return False

def get_servers_without_fingerprints(limit):
    sql = f"""
    SELECT r.server_id, r.name, r.description, r.url, r.trust_score, r.verdict
    FROM mcp_server_registry r
    LEFT JOIN mcp_fingerprints f ON r.server_id = f.server_id
    WHERE f.server_id IS NULL
    LIMIT {limit}
    """
    result = ws_query(sql)
    rows = result.get('rows', [])
    log(f"Found {len(rows)} servers needing fingerprints (limit={limit})")
    return rows

def compute_fingerprint(server_row):
    fp = sys.modules.get('mcp_fingerprinter')
    if fp is None:
        import mcp_fingerprinter
        fp = mcp_fingerprinter
    if hasattr(fp, 'fingerprint'):
        return fp.fingerprint(server_row)
    if hasattr(fp, 'compute_fingerprint'):
        return fp.compute_fingerprint(server_row)
    if hasattr(fp, 'generate_fingerprint'):
        return fp.generate_fingerprint(server_row)
    if hasattr(fp, 'main'):
        return fp.main(server_row)
    raise RuntimeError("mcp_fingerprinter has no recognized fingerprint entry point")

def run():
    log(f"Starting {SERVICE_NAME}...")
    if not check_single_instance():
        sys.exit(1)
    
    def sig_handler(signum, frame):
        log("Received shutdown signal, exiting gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    
    send_heartbeat()
    log(f"{SERVICE_NAME} is running. Poll every {POLL_SECS}s, batch limit {BATCH_LIMIT}.")
    
    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            servers = get_servers_without_fingerprints(BATCH_LIMIT)
            processed = 0
            
            for row in servers:
                server_id = row.get('server_id')
                if not server_id:
                    log("Skipping row with no server_id")
                    continue
                
                try:
                    fp_result = compute_fingerprint(row)
                    
                    if fp_result and isinstance(fp_result, dict):
                        fingerprint_hash = fp_result.get('fingerprint_hash', '')
                        tool_count = fp_result.get('tool_count', 0)
                        prompt_count = fp_result.get('prompt_count', 0)
                        resource_count = fp_result.get('resource_count', 0)
                        computed_at = fp_result.get('computed_at', utc_now.isoformat())
                        
                        record = {
                            'server_id': server_id,
                            'fingerprint_hash': fingerprint_hash,
                            'tool_count': tool_count,
                            'prompt_count': prompt_count,
                            'resource_count': resource_count,
                            'computed_at': computed_at,
                        }
                        
                        ws_write({'table': 'mcp_fingerprints', 'rows': [record]})
                        processed += 1
                        
                        hash_preview = fingerprint_hash[:16] if fingerprint_hash else 'empty'
                        log(f"Fingerprinted {server_id}: hash={hash_preview}...")
                    else:
                        log(f"No fingerprint data for server {server_id}")
                
                except Exception as e:
                    log(f"Per-server error for {server_id}: {type(e).__name__}: {e}")
                    continue
            
            send_heartbeat()
            log(f"Batch complete. Fingerprinted {processed} servers. Sleeping {POLL_SECS}s...")
            
        except Exception as e:
            log(f"Batch loop error: {type(e).__name__}: {e}")
        
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()