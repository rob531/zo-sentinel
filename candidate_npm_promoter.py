#!/usr/bin/env python3
"""
candidate_npm_promoter.py
Long-running daemon that promotes npm candidates to mcp_server_registry.
Every 300s queries candidates, fetches npmjs.org metadata, writes to registry.
"""
import hashlib
import json
import logging
import os
import requests
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = 'candidate_npm_promoter'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 30
POLL_SECS = 300
LOG_FILE = '/home/workspace/logs/candidate_npm_promoter.log'
LOCK_FILE = '/home/workspace/logs/candidate_npm_promoter.lock'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
NPM_TIMEOUT = 8
FETCH_DELAY = 0.25
USER_AGENT = 'zo-sentinel/1.0 (mcp-trust-intelligence)'

logger = None

def setup_logging():
    global logger
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.INFO)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_SERVICE_URL

def get_execute_url():
    return EXECUTE_SERVICE_URL

def get_db_path():
    return '/home/workspace/zo_sentinel/sentinel.db'

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                existing_pid = int(f.read().strip())
            if existing_pid != pid and os.path.exists(f'/proc/{existing_pid}'):
                logger.info(f"Instance already running with PID {existing_pid}, exiting")
                sys.exit(0)
        except (ValueError, IOError):
            pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(pid))
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))

def remove_pid_file():
    for f in [LOCK_FILE, PID_FILE]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {sql[:100]} - {e}")
        return {'rows': [], 'count': 0}

def ws_write(table, rows):
    try:
        payload = {'table': table, 'rows': rows, 'wait': True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Write failed to {table}: {e}")
        return {'ok': False}

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Execute failed: {sql[:100]} - {e}")
        return {'ok': False}

def send_heartbeat():
    try:
        payload = {'table': 'service_health', 'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()}, 'wait': True}
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def extract_npm_package_name(candidate):
    name = candidate.get('candidate_name', '')
    url = candidate.get('candidate_url', '')
    
    if '/package/' in url:
        parts = url.split('/package/')
        if len(parts) > 1:
            pkg = parts[1].split('/')[0].split('?')[0]
            return pkg
    
    if name.startswith('npm/'):
        return name[4:]
    if name.startswith('io.modelcontextprotocol/'):
        return name.replace('io.modelcontextprotocol/', '')
    
    return None

def fetch_npm_metadata(pkg_name):
    url = f'https://registry.npmjs.org/{pkg_name}/latest'
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=NPM_TIMEOUT)
        if resp.status_code == 404:
            return None, 'not_found'
        if resp.status_code != 200:
            return None, 'http_error'
        data = resp.json()
        return data, 'success'
    except requests.exceptions.Timeout:
        return None, 'timeout'
    except requests.exceptions.RequestException as e:
        return None, 'network_error'
    except json.JSONDecodeError:
        return None, 'json_error'

def compute_server_id(pkg_name):
    hash_input = f"npm|{pkg_name}"
    return hashlib.md5(hash_input.encode()).hexdigest()

def server_exists(server_id):
    result = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE server_id = '{server_id}'")
    return result.get('count', 0) > 0

def promote_candidate(candidate, npm_data):
    pkg_name = extract_npm_package_name(candidate)
    if not pkg_name:
        return False, 'no_package_name'
    
    server_id = compute_server_id(pkg_name)
    
    if server_exists(server_id):
        return False, 'already_present'
    
    name = npm_data.get('name', pkg_name)
    version = npm_data.get('version', '')
    description = npm_data.get('description', candidate.get('candidate_description', ''))
    if description:
        description = description[:1000]
    
    repo = npm_data.get('repository', {})
    repo_url = ''
    if isinstance(repo, dict):
        repo_url = repo.get('url', '')
    elif isinstance(repo, str):
        repo_url = repo
    
    author = npm_data.get('author', {})
    author_str = ''
    if isinstance(author, dict):
        author_str = author.get('name', '')
    elif isinstance(author, str):
        author_str = author
    
    maintainers = npm_data.get('maintainers', [])
    maintainer_count = len(maintainers) if maintainers else 0
    
    dependencies = npm_data.get('dependencies', {})
    dependency_count = len(dependencies) if dependencies else 0
    
    time_data = npm_data.get('time', {})
    created = time_data.get('created', '')
    modified = time_data.get('modified', '')
    
    now_ts = datetime.now(timezone.utc).isoformat()
    
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - created_dt).days
        except:
            age_days = 0
    else:
        age_days = 0
    
    metadata = {
        'version': version,
        'repository_url': repo_url,
        'author': author_str,
        'maintainer_count': maintainer_count,
        'dependency_count': dependency_count,
        'age_days': age_days,
        'dist_tarball': npm_data.get('dist', {}).get('tarball', ''),
        'npm_created': created,
        'npm_modified': modified
    }
    
    pkg_url = npm_data.get('homepage', '')
    if not pkg_url:
        pkg_url = f"https://www.npmjs.com/package/{pkg_name}"
    
    row = {
        'server_id': server_id,
        'name': name,
        'registry_source': 'npm',
        'url': pkg_url,
        'description': description,
        'trust_score': 0.0,
        'verdict': 'unknown',
        'verdict_reasoning': '',
        'confidence': 0.0,
        'last_assessed': None,
        'first_seen': created if created else now_ts,
        'last_seen': now_ts,
        'last_scanned': None,
        'scan_count': 0,
        'risk_tier': 'unassessed',
        'metadata': json.dumps(metadata)
    }
    
    result = ws_write('mcp_server_registry', row)
    if not result.get('ok'):
        return False, 'write_failed'
    
    return True, server_id

def mark_promoted(candidate_id):
    sql = f"UPDATE mcp_discovery_candidates SET promoted=TRUE, reviewed_at='{datetime.now(timezone.utc).isoformat()}' WHERE id={candidate_id}"
    return ws_execute(sql)

def get_candidates():
    sql = """
    SELECT id, candidate_name, candidate_url, candidate_description 
    FROM mcp_discovery_candidates 
    WHERE (promoted IS FALSE OR promoted IS NULL) 
    AND (candidate_url LIKE '%npmjs.com/package/%' OR candidate_name LIKE 'npm/%' OR candidate_name LIKE 'io.modelcontextprotocol/%') 
    LIMIT 50
    """
    result = ws_query(sql)
    return result.get('rows', [])

def cycle():
    candidates = get_candidates()
    
    promoted_count = 0
    npm_404_count = 0
    npm_error_count = 0
    skip_already_present_count = 0
    
    for candidate in candidates:
        candidate_id = candidate.get('id')
        pkg_name = extract_npm_package_name(candidate)
        
        if not pkg_name:
            logger.warning(f"No package name extracted for candidate {candidate_id}")
            continue
        
        npm_data, status = fetch_npm_metadata(pkg_name)
        
        if status == 'not_found':
            npm_404_count += 1
            logger.info(f"npm package not found: {pkg_name}")
            mark_promoted(candidate_id)
            continue
        
        if status != 'success':
            npm_error_count += 1
            logger.warning(f"npm fetch failed for {pkg_name}: {status}")
            continue
        
        success, result = promote_candidate(candidate, npm_data)
        
        if success:
            promoted_count += 1
            logger.info(f"Promoted candidate {candidate_id} as server {result}")
        else:
            if result == 'already_present':
                skip_already_present_count += 1
                logger.info(f"Server already present for {pkg_name}")
            else:
                logger.warning(f"Failed to promote {candidate_id}: {result}")
        
        mark_promoted(candidate_id)
        time.sleep(FETCH_DELAY)
    
    logger.info(f"Batch complete: promoted={promoted_count}, npm_404={npm_404_count}, npm_error={npm_error_count}, skip_already_present={skip_already_present_count}")

def run():
    setup_logging()
    logger.info(f"{SERVICE_NAME} starting")
    
    import signal as sig_module
    sig_module.signal(sig_module.SIGTERM, signal_handler)
    sig_module.signal(sig_module.SIGINT, signal_handler)
    
    check_single_instance()
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    try:
        while True:
            cycle()
            time.sleep(POLL_SECS)
    except Exception as e:
        logger.error(f"Run loop error: {e}")
    finally:
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} stopped")

if __name__ == '__main__':
    run()