import sys
sys.path.insert(0, '/home/workspace')

import json
import hashlib
import time
import os
import signal
from datetime import datetime, timezone

import requests

SERVICE_NAME = 'candidate_smithery_promoter'
PORT = None  # No FastAPI port, pure daemon
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_FILE = f'/tmp/{SERVICE_NAME}.log'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
WRITE_URL = 'http://127.0.0.1:8772/write'
POLL_SECS = 600
FETCH_DELAY_MS = 250
FETCH_TIMEOUT = 15

# Smithery API research (2025-06-29):
# Endpoint pattern: https://smithery.ai/api/server/<slug>
# Response shape (confirmed via web research):
#   - GET /api/server/<slug> returns JSON with fields:
#     name, description, serverId, tools[], etc.
#   - HTTP 404 if slug not found
#   - HTTP 200 with valid server object on success
SMITHERY_API_BASE = 'https://smithery.ai/api/server'


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def signal_handler(sig, frame):
    log(f"Caught signal {sig}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.unlink(PID_FILE)
    except Exception as e:
        log(f"Warning: could not remove PID file: {e}")


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid != pid and os.path.exists(f'/proc/{old_pid}'):
                log(f"Another instance running with PID {old_pid}, exiting")
                sys.exit(1)
        except (ValueError, IOError):
            pass
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(pid))
    except Exception as e:
        log(f"Warning: could not write PID file: {e}")


def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query error: {e}")
        return {'rows': [], 'count': 0}


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows}
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write error for {table}: {e}")
        return None


def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Execute error: {e}")
        return None


def send_heartbeat():
    try:
        requests.post(WRITE_URL, json={
            'table': 'service_health',
            'rows': [{'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()}]
        }, timeout=10)
    except Exception as e:
        log(f"Heartbeat error: {e}")


def fetch_smithery_metadata(slug):
    url = f"{SMITHERY_API_BASE}/{slug}"
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT)
        if resp.status_code == 404:
            return None, 'not_found_in_smithery_api', 404
        if resp.status_code == 200:
            try:
                data = resp.json()
                return data, None, 200
            except json.JSONDecodeError:
                return None, 'invalid_json_response', resp.status_code
        else:
            return None, f'http_error_{resp.status_code}', resp.status_code
    except requests.exceptions.Timeout:
        return None, 'request_timeout', None
    except requests.exceptions.ConnectionError:
        return None, 'connection_error', None
    except Exception as e:
        return None, f'exception_{str(e)}', None


def parse_smithery_slug(candidate_name):
    prefix = 'ai.smithery/'
    if candidate_name.startswith(prefix):
        return candidate_name[len(prefix):]
    return None


def mint_server_id(slug):
    raw = f"smithery|{slug}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def get_pending_smithery_candidates():
    sql = """
    SELECT id, candidate_name, candidate_url, candidate_description
    FROM mcp_discovery_candidates
    WHERE (promoted IS FALSE OR promoted IS NULL)
      AND candidate_name LIKE 'ai.smithery/%'
    LIMIT 50
    """
    result = ws_query(sql)
    return result.get('rows', [])


def mark_candidate_promoted(candidate_id, skip_reason=None):
    now = datetime.now(timezone.utc).isoformat()
    if skip_reason:
        sql = f"""
        UPDATE mcp_discovery_candidates
        SET promoted = TRUE,
            reviewed_at = '{now}',
            metadata = json_set(COALESCE(metadata, '{{}}'), '$.skip_reason', '{skip_reason}')
        WHERE id = {candidate_id}
        """
    else:
        sql = f"""
        UPDATE mcp_discovery_candidates
        SET promoted = TRUE,
            reviewed_at = '{now}'
        WHERE id = {candidate_id}
        """
    ws_execute(sql)


def ensure_registry_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        url VARCHAR,
        description TEXT,
        trust_score DOUBLE,
        verdict VARCHAR,
        registry_source VARCHAR,
        scan_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)


def write_to_registry(server_data):
    rows = [server_data]
    return ws_write('mcp_server_registry', rows)


def promote_candidate(candidate):
    candidate_id = candidate.get('id')
    candidate_name = candidate.get('candidate_name', '')
    candidate_url = candidate.get('candidate_url', '')
    candidate_description = candidate.get('candidate_description', '')

    slug = parse_smithery_slug(candidate_name)
    if not slug:
        log(f"Cannot parse Smithery slug from: {candidate_name}")
        mark_candidate_promoted(candidate_id, 'parse_error')
        return False

    log(f"Fetching Smithery metadata for slug: {slug}")
    metadata, skip_reason, status_code = fetch_smithery_metadata(slug)

    if skip_reason:
        log(f"Smithery API returned {status_code} for slug '{slug}': {skip_reason}")
        mark_candidate_promoted(candidate_id, skip_reason)
        return False

    if not metadata:
        log(f"No metadata returned for slug: {slug}")
        mark_candidate_promoted(candidate_id, 'no_metadata')
        return False

    server_id = mint_server_id(slug)

    name = metadata.get('name') or candidate_name
    url = metadata.get('url') or metadata.get('serverUrl') or metadata.get('npmUrl') or candidate_url
    description = metadata.get('description') or candidate_description

    server_data = {
        'server_id': server_id,
        'name': name,
        'url': url,
        'description': description,
        'trust_score': None,
        'verdict': None,
        'registry_source': 'smithery',
        'scan_count': 0
    }

    success = write_to_registry(server_data)
    if success:
        log(f"Promoted candidate {candidate_name} -> server_id={server_id}")
        mark_candidate_promoted(candidate_id)
        return True
    else:
        log(f"Failed to write to registry for {candidate_name}")
        return False


def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    log(f"Starting {SERVICE_NAME} daemon")

    ensure_registry_table()

    cycle_count = 0
    while True:
        cycle_count += 1
        log(f"=== Cycle {cycle_count} ===")
        send_heartbeat()

        candidates = get_pending_smithery_candidates()
        log(f"Found {len(candidates)} pending Smithery candidates")

        for candidate in candidates:
            success = promote_candidate(candidate)
            if not success:
                log(f"Candidate promotion failed: {candidate.get('candidate_name')}")
            time.sleep(FETCH_DELAY_MS / 1000.0)

        log(f"Cycle {cycle_count} complete, sleeping {POLL_SECS}s")
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()