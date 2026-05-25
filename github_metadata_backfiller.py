import os
import re
import json
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

SERVICE_NAME = "github_metadata_backfiller"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
POLL_SECS = 600
GITHUB_API_BASE = "https://api.github.com"
FETCH_TIMEOUT = 8

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing != str(pid):
            try:
                existing_pid = int(existing)
                os.kill(existing_pid, 0)
                log.warning(f"Another instance running: {existing_pid}")
                return False
            except (OSError, ValueError):
                log.info("Stale PID file found, will replace")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f"Started with PID {pid}")
    return True


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    exit(0)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        if resp.status_code == 200:
            return True
        log.error(f"ws_write failed: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Query write_service."""
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        log.error(f"ws_query failed: {resp.status_code} {resp.text}")
        return None
    except Exception as e:
        log.error(f"ws_query error: {e}")
        return None


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML on write_service."""
    try:
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=15
        )
        if resp.status_code == 200:
            return True
        log.error(f"ws_execute failed: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        log.error(f"ws_execute error: {e}")
        return False


def send_heartbeat():
    """Update service health."""
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()}],
                "wait": True
            },
            timeout=5
        )
    except Exception:
        pass


def ensure_tables():
    """Create required tables if not exist."""
    log.info("Ensuring tables exist...")

    ws_execute("""
        CREATE TABLE IF NOT EXISTS github_velocity (
            server_id VARCHAR,
            stars INTEGER,
            forks INTEGER,
            days_since_push INTEGER,
            archived BOOLEAN,
            last_updated TIMESTAMP
        )
    """)

    log.info("Tables ready")


def get_github_token() -> Optional[str]:
    """Get GitHub token from environment."""
    return os.environ.get('GITHUB_TOKEN')


def get_github_headers(token: Optional[str]) -> Dict[str, str]:
    """Build GitHub API headers."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ZO-Sentinel-GitHub-Metadata/1.0"
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def parse_github_url(url: str) -> Optional[tuple]:
    """Extract owner and repo from GitHub URL."""
    patterns = [
        r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$',
        r'https?://api\.github\.com/repos/([^/]+)/([^/]+?)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None


def parse_rate_limit_info(resp: requests.Response) -> tuple:
    """Extract rate limit info from response headers."""
    remaining = int(resp.headers.get('X-RateLimit-Remaining', '9999'))
    reset_ts = int(resp.headers.get('X-RateLimit-Reset', '0'))
    return remaining, reset_ts


def handle_rate_limit(remaining: int, reset_ts: int):
    """Sleep if rate limited."""
    if remaining < 5:
        now = time.time()
        wait_seconds = max(1, reset_ts - now + 5)
        log.warning(f"Rate limit low ({remaining}), waiting {wait_seconds:.0f}s")
        time.sleep(min(wait_seconds, 60))


def fetch_github_repo(owner: str, repo: str, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch repository metadata from GitHub API."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    headers = get_github_headers(token)

    try:
        resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        remaining, reset_ts = parse_rate_limit_info(resp)
        handle_rate_limit(remaining, reset_ts)

        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            log.warning(f"Repo not found: {owner}/{repo}")
        elif resp.status_code == 403:
            log.error(f"Access forbidden for {owner}/{repo}")
        else:
            log.error(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        return None
    except requests.exceptions.Timeout:
        log.error(f"Timeout fetching {owner}/{repo}")
        return None
    except Exception as e:
        log.error(f"Error fetching {owner}/{repo}: {e}")
        return None


def compute_days_since_push(pushed_at: Optional[str]) -> Optional[int]:
    """Calculate days since last push."""
    if not pushed_at:
        return None
    try:
        pushed_dt = datetime.fromisoformat(pushed_at.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        return (now - pushed_dt).days
    except:
        return None


def build_metadata_dict(repo_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build metadata dict from GitHub response."""
    return {
        "stargazers_count": repo_data.get("stargazers_count"),
        "forks_count": repo_data.get("forks_count"),
        "open_issues_count": repo_data.get("open_issues_count"),
        "pushed_at": repo_data.get("pushed_at"),
        "created_at": repo_data.get("created_at"),
        "updated_at": repo_data.get("updated_at"),
        "default_branch": repo_data.get("default_branch"),
        "license": repo_data.get("license", {}).get("spdx_id") if repo_data.get("license") else None,
        "archived": repo_data.get("archived", False),
        "disabled": repo_data.get("disabled", False),
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


def write_registry_facts(server_id: str, repo_data: Dict[str, Any]):
    """Write evidence to mcp_registry_facts."""
    facts = [
        {"server_id": server_id, "fact_type": "github_stars", "fact_value": str(repo_data.get("stargazers_count", 0)), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_forks", "fact_value": str(repo_data.get("forks_count", 0)), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_open_issues", "fact_value": str(repo_data.get("open_issues_count", 0)), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_last_push", "fact_value": repo_data.get("pushed_at", ""), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_created_at", "fact_value": repo_data.get("created_at", ""), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_license", "fact_value": repo_data.get("license", {}).get("spdx_id", "") if repo_data.get("license") else "none", "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_archived", "fact_value": str(repo_data.get("archived", False)), "evidence": json.dumps({"source": "github_api"})},
        {"server_id": server_id, "fact_type": "github_disabled", "fact_value": str(repo_data.get("disabled", False)), "evidence": json.dumps({"source": "github_api"})},
    ]
    ws_write("mcp_registry_facts", facts)


def write_github_velocity(server_id: str, repo_data: Dict[str, Any]):
    """Write velocity data to github_velocity table."""
    stars = repo_data.get("stargazers_count", 0)
    forks = repo_data.get("forks_count", 0)
    days_since_push = compute_days_since_push(repo_data.get("pushed_at"))
    archived = repo_data.get("archived", False)

    velocity_row = {
        "server_id": server_id,
        "stars": stars,
        "forks": forks,
        "days_since_push": days_since_push if days_since_push else 9999,
        "archived": archived,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    ws_write("github_velocity", [velocity_row])


def get_pending_github_servers() -> List[Dict[str, Any]]:
    """Query servers needing GitHub metadata refresh."""
    sql = """
        SELECT server_id, name, url, metadata, scan_count, last_scanned
        FROM mcp_server_registry
        WHERE registry_source IN ('github', 'github_topic')
        AND (
            metadata IS NULL
            OR metadata = '{}'
            OR metadata = ''
            OR last_scanned IS NULL
            OR last_scanned < now() - INTERVAL 14 DAY
        )
        LIMIT 30
    """
    result = ws_query(sql)
    return result if result else []


def update_server_metadata(server_id: str, metadata: Dict[str, Any]):
    """Update mcp_server_registry with new metadata."""
    metadata_json = json.dumps(metadata)
    sql = f"""
        UPDATE mcp_server_registry
        SET metadata = '{metadata_json.replace("'", "''")}',
            last_scanned = now(),
            scan_count = scan_count + 1
        WHERE server_id = '{server_id}'
    """
    return ws_execute(sql)


def process_server(server: Dict[str, Any], token: Optional[str]) -> bool:
    """Process a single GitHub server."""
    server_id = server.get("server_id")
    url = server.get("url", "")

    if not server_id or not url:
        return False

    parsed = parse_github_url(url)
    if not parsed:
        log.warning(f"Could not parse GitHub URL: {url}")
        return False

    owner, repo = parsed
    log.info(f"Fetching metadata for {owner}/{repo} (server_id: {server_id})")

    repo_data = fetch_github_repo(owner, repo, token)
    if not repo_data:
        log.warning(f"Failed to fetch data for {owner}/{repo}")
        return False

    metadata = build_metadata_dict(repo_data)

    if update_server_metadata(server_id, metadata):
        write_registry_facts(server_id, repo_data)
        write_github_velocity(server_id, repo_data)
        log.info(f"Updated metadata for {server_id}: stars={metadata.get('stargazers_count')}, forks={metadata.get('forks_count')}")
        return True
    else:
        log.error(f"Failed to update metadata for {server_id}")
        return False


def cycle():
    """Run one cycle of metadata backfill."""
    log.info("Starting GitHub metadata backfill cycle...")

    token = get_github_token()
    if not token:
        log.warning("GITHUB_TOKEN not set, API calls may be rate-limited")

    servers = get_pending_github_servers()
    log.info(f"Found {len(servers)} servers needing metadata refresh")

    success_count = 0
    fail_count = 0

    for server in servers:
        if process_server(server, token):
            success_count += 1
        else:
            fail_count += 1

    log.info(f"Cycle complete: {success_count} success, {fail_count} failures")
    return success_count, fail_count


def run():
    """Main daemon loop."""
    if not check_single_instance():
        log.error("Another instance is running, exiting")
        return

    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log.info(f"Starting {SERVICE_NAME} daemon on port {SERVICE_PORT}")
    ensure_tables()

    start_time = time.time()

    while True:
        try:
            send_heartbeat()
            cycle()
        except Exception as e:
            log.error(f"Error in main loop: {e}")

        elapsed = time.time() - start_time
        log.info(f"Sleeping for {POLL_SECS}s (elapsed: {elapsed:.0f}s)")
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()