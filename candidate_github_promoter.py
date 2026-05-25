import requests
import json
import hashlib
import datetime
import threading
import time
import os
import sys
import pathlib
import re
import logging

SERVICE_NAME = "candidate_github_promoter"
SERVICE_PORT = None
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
LOCK_FILE = "/home/workspace/logs/candidate_github_promoter.lock"
PID_FILE = "/tmp/candidate_github_promoter.pid"
HEARTBEAT_INTERVAL = 30
CYCLE_INTERVAL = 300
GITHUB_API_BASE = "https://api.github.com/repos"
FETCH_TIMEOUT = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/workspace/logs/candidate_github_promoter.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def log_info(msg):
    log.info(msg)


def log_error(msg):
    log.error(msg)


def send_heartbeat():
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.datetime.utcnow().isoformat()
            }
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        log_error(f"Heartbeat failed: {e}")


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def check_single_instance():
    lock_path = pathlib.Path(LOCK_FILE)
    pid_path = pathlib.Path(PID_FILE)
    
    if lock_path.exists():
        try:
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())
            if os.path.exists("/proc/" + str(old_pid)):
                log_error(f"Another instance is running (PID {old_pid})")
                return False
            else:
                log_info(f"Stale lock file found (PID {old_pid}), reclaiming")
        except Exception:
            pass
    
    current_pid = os.getpid()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            f.write(str(current_pid))
        with open(pid_path, "w") as f:
            f.write(str(current_pid))
    except Exception as e:
        log_error(f"Failed to write lock file: {e}")
        return False
    
    return True


def remove_pid_file():
    try:
        pathlib.Path(LOCK_FILE).unlink(missing_ok=True)
        pathlib.Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    log_info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def is_valid_hex(server_id):
    return bool(re.fullmatch(r'[0-9a-f]{16}', server_id))


def ws_query(sql):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log_error(f"ws_query failed: {e}")
        return None


def ws_write(table, rows):
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log_error(f"ws_write failed: {e}")
        return None


def ws_execute(sql):
    try:
        payload = {"sql": sql}
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log_error(f"ws_execute failed: {e}")
        return None


def get_github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zo-sentinel/1.0"
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_rate_limit_reset():
    token = os.environ.get("GITHUB_TOKEN")
    return bool(token)


def parse_github_url(url):
    pattern = r'https://github\.com/([^/]+)/([^/]+)/?'
    match = re.search(pattern, url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def mint_server_id(owner, repo):
    raw = f"github|{owner}/{repo}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def fetch_github_repo(owner, repo):
    url = f"{GITHUB_API_BASE}/{owner}/{repo}"
    headers = get_github_headers()
    
    try:
        resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        
        if resp.status_code == 404:
            return {"error": "not_found"}
        elif resp.status_code == 403:
            reset_header = resp.headers.get("X-RateLimit-Reset")
            if reset_header:
                reset_time = int(reset_header)
                current_time = int(time.time())
                wait_seconds = max(0, reset_time - current_time) + 30
                log_info(f"Rate limited, waiting until reset: {wait_seconds}s")
                time.sleep(wait_seconds)
            else:
                log_info("Rate limited (no reset header), backing off 60s")
                time.sleep(60)
            return {"error": "rate_limited"}
        elif resp.status_code != 200:
            return {"error": f"http_{resp.status_code}"}
        
        data = resp.json()
        return {"success": True, "data": data}
        
    except Exception as e:
        return {"error": str(e)}


def compute_days_since_push(pushed_at_str):
    if not pushed_at_str:
        return None
    try:
        pushed = datetime.datetime.strptime(pushed_at_str, "%Y-%m-%dT%H:%M:%SZ")
        now = datetime.datetime.utcnow()
        return (now - pushed).days
    except Exception:
        return None


def now_iso():
    return datetime.datetime.utcnow().isoformat()


def build_registry_row(server_id, github_data, owner, repo):
    d = github_data
    pushed_at = d.get("pushed_at", "")
    created_at = d.get("created_at", "")
    days_since_push = compute_days_since_push(pushed_at)
    
    metadata = {
        "stargazers_count": d.get("stargazers_count", 0),
        "forks_count": d.get("forks_count", 0),
        "open_issues_count": d.get("open_issues_count", 0),
        "pushed_at": pushed_at,
        "archived": d.get("archived", False),
        "disabled": d.get("disabled", False),
        "license_spdx": (d.get("license") or {}).get("spdx_id"),
        "owner_login": d.get("owner", {}).get("login"),
        "owner_type": d.get("owner", {}).get("type"),
        "days_since_push": days_since_push,
        "observed_in_registries": ["github"]
    }
    
    row = {
        "server_id": server_id,
        "name": (d.get("full_name") or f"{owner}/{repo}")[:255],
        "registry_source": "github",
        "url": d.get("html_url") or f"https://github.com/{owner}/{repo}",
        "description": (d.get("description") or "")[:1000],
        "trust_score": 0.0,
        "verdict": "unknown",
        "verdict_reasoning": "",
        "confidence": 0.0,
        "last_assessed": None,
        "first_seen": created_at or now_iso(),
        "last_seen": now_iso(),
        "last_scanned": now_iso(),
        "scan_count": 1,
        "risk_tier": "unassessed",
        "metadata": json.dumps(metadata)
    }
    
    return row


def mark_promoted(candidate_id, skip_reason=None):
    now = now_iso()
    if skip_reason:
        sql = f"UPDATE mcp_discovery_candidates SET promoted=TRUE, reviewed_at='{now}', metadata=JSON_SET(COALESCE(metadata,'{{}}'),'$.skip_reason','{skip_reason}') WHERE id={candidate_id}"
    else:
        sql = f"UPDATE mcp_discovery_candidates SET promoted=TRUE, reviewed_at='{now}' WHERE id={candidate_id}"
    ws_execute(sql)


def cycle():
    log_info("Starting GitHub candidate promotion cycle")
    
    sql = """
        SELECT id, candidate_name, candidate_url, candidate_description 
        FROM mcp_discovery_candidates 
        WHERE (promoted IS FALSE OR promoted IS NULL) 
        AND discovered_in_directory='github_topic' 
        LIMIT 50
    """
    
    result = ws_query(sql)
    if not result:
        log_error("Failed to query candidates")
        return
    
    rows = result.get("rows", [])
    if not rows:
        log_info("No pending GitHub candidates found")
        return
    
    queried = len(rows)
    promoted = 0
    skipped = 0
    not_found = 0
    errors = 0
    rate_limited = False
    
    is_authenticated = bool(os.environ.get("GITHUB_TOKEN"))
    sleep_duration = 1 if is_authenticated else 6
    
    for row in rows:
        candidate_id = row.get("id")
        candidate_url = row.get("candidate_url", "")
        
        owner, repo = parse_github_url(candidate_url)
        if not owner or not repo:
            log_info(f"Cannot parse GitHub URL for candidate {candidate_id}: {candidate_url}")
            mark_promoted(candidate_id, "url_unparseable")
            skipped += 1
            time.sleep(0.5)
            continue
        
        server_id = mint_server_id(owner, repo)
        
        if not is_valid_hex(server_id):
            log_error(f"Invalid server_id generated: {server_id}")
            mark_promoted(candidate_id, "invalid_server_id")
            skipped += 1
            continue
        
        check_sql = f"SELECT 1 FROM mcp_server_registry WHERE server_id = '{server_id}'"
        check_result = ws_query(check_sql)
        if check_result and check_result.get("rows"):
            log_info(f"Server {server_id} already exists in registry, marking promoted")
            mark_promoted(candidate_id)
            promoted += 1
            time.sleep(sleep_duration)
            continue
        
        repo_result = fetch_github_repo(owner, repo)
        
        if repo_result.get("error") == "not_found":
            log_info(f"GitHub repo not found (404): {owner}/{repo}")
            mark_promoted(candidate_id, "github_404")
            not_found += 1
            time.sleep(sleep_duration)
            continue
        elif repo_result.get("error") == "rate_limited":
            rate_limited = True
            break
        elif repo_result.get("error"):
            log_error(f"Error fetching {owner}/{repo}: {repo_result.get('error')}")
            errors += 1
            time.sleep(sleep_duration)
            continue
        
        github_data = repo_result.get("data")
        registry_row = build_registry_row(server_id, github_data, owner, repo)
        
        write_result = ws_write("mcp_server_registry", registry_row)
        if write_result:
            mark_promoted(candidate_id)
            promoted += 1
            log_info(f"Promoted {server_id}: {registry_row['name']}")
        else:
            log_error(f"Failed to write registry row for {server_id}")
            errors += 1
        
        time.sleep(sleep_duration)
    
    log_info(f"batch done queried={queried} promoted={promoted} skipped={skipped} 404s={not_found} errors={errors}")
    
    if rate_limited:
        log_info("Cycle ended due to rate limiting")


def run():
    log_info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log_error("Failed to acquire lock - another instance may be running")
        sys.exit(1)
    
    try:
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
    except Exception as e:
        log_error(f"Failed to start heartbeat thread: {e}")
    
    try:
        while True:
            try:
                cycle()
            except Exception as e:
                log_error(f"Cycle failed with exception: {e}")
            
            time.sleep(CYCLE_INTERVAL)
    except KeyboardInterrupt:
        log_info("Received keyboard interrupt")
    finally:
        remove_pid_file()
        log_info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    run()