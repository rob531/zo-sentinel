#!/usr/bin/env python3
"""
github_repo_velocity.py -- ZO-SENTINEL GitHub supply chain velocity analyser.
Detects suspicious GitHub activity patterns indicating potential rug-pulls:
- High commit velocity from new contributors
- Massive PRs from unknown contributors
- Recent repo ownership transfers
"""
import os
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from threading import Lock

log = logging.getLogger(__name__)

SERVICE_NAME = "github_repo_velocity"
PORT = 0

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"

GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 43200
MAX_RETRIES = 3
RETRY_BACKOFF = 2

_pid_file = "/tmp/zo_sentinel_github_repo_velocity.pid"
_shutdown_flag = False
_state_lock = Lock()
_last_run = None


def get_github_headers():
    """Get headers for GitHub API requests."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ZO-SENTINEL-GitHub-Velocity-Analyser/1.0"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def rate_limit_call():
    """Enforce rate limiting between GitHub API calls."""
    time.sleep(1)


def get_with_retry(url: str, params: dict = None) -> Optional[dict]:
    """Execute GET request with retry logic and rate limiting."""
    headers = get_github_headers()
    for attempt in range(MAX_RETRIES):
        try:
            rate_limit_call()
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 403:
                log.warning(f"GitHub API rate limit hit, waiting 60s...")
                time.sleep(60)
                continue
            if response.status_code == 404:
                log.warning(f"Resource not found: {url}")
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            log.warning(f"Request attempt {attempt + 1}/{MAX_RETRIES} failed for {url}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
            else:
                log.error(f"All retries exhausted for {url}")
                return None
    return None


def check_single_instance() -> bool:
    """Ensure only one instance of this service is running."""
    import os
    import sys
    
    if os.path.exists(_pid_file):
        try:
            with open(_pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance is running with PID {old_pid}")
                return False
            except OSError:
                log.info(f"Stale PID file found, removing...")
                os.remove(_pid_file)
        except (ValueError, IOError) as e:
            log.warning(f"Error reading PID file: {e}")
    
    try:
        with open(_pid_file, 'w') as f:
            f.write(str(os.getpid()))
    except IOError as e:
        log.error(f"Cannot create PID file: {e}")
    
    return True


def send_heartbeat() -> bool:
    """Send heartbeat to write service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "running"
            }
        }
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        log.warning(f"Heartbeat failed: {e}")
        return False


def ws_query(sql: str, params: dict = None) -> list:
    """Execute query via write_service query endpoint."""
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": params} if params else {"sql": sql},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result.get('data', result.get('rows', []))
        log.error(f"Query failed: {response.status_code} - {response.text}")
        return []
    except requests.exceptions.RequestException as e:
        log.error(f"Query error: {e}")
        return []


def ws_write(table: str, rows: dict) -> bool:
    """Write data to write_service."""
    try:
        payload = {"table": table, "rows": rows}
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        if response.status_code == 200:
            return True
        log.error(f"Write failed for {table}: {response.status_code} - {response.text}")
        return False
    except requests.exceptions.RequestException as e:
        log.error(f"Write error for {table}: {e}")
        return False


def parse_github_url(url: str) -> Optional[tuple]:
    """Parse owner and repo from GitHub URL."""
    if not url:
        return None
    try:
        clean_url = url.replace('https://github.com/', '').replace('http://github.com/', '')
        clean_url = clean_url.replace('.git', '').strip('/')
        parts = clean_url.split('/')
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception as e:
        log.warning(f"Failed to parse GitHub URL {url}: {e}")
    return None


def get_recent_commits(owner: str, repo: str, days: int = 30) -> tuple:
    """Fetch recent commits and calculate velocity."""
    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    params = {"per_page": 30, "since": since_date}
    
    commits = get_with_retry(url, params)
    if not commits:
        return [], 0.0
    
    if isinstance(commits, dict) and 'commit' in commits:
        commits = [commits]
    
    weeks = max(1, days / 7)
    velocity = len(commits) / weeks if commits else 0.0
    
    return commits, velocity


def get_contributors(owner: str, repo: str) -> list:
    """Fetch contributors to detect churn."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contributors"
    params = {"per_page": 10, "anon": "true"}
    
    contributors = get_with_retry(url, params)
    return contributors if isinstance(contributors, list) else []


def get_merged_prs(owner: str, repo: str) -> list:
    """Fetch merged PRs to detect massive PRs from unknown contributors."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
    params = {"state": "merged", "per_page": 10, "sort": "updated", "direction": "desc"}
    
    prs = get_with_retry(url, params)
    return prs if isinstance(prs, list) else []


def check_repo_transfer(owner: str, repo: str) -> tuple:
    """Check if repository was recently transferred to a new owner."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    repo_data = get_with_retry(url)
    
    if not repo_data:
        return False, None
    
    if 'head' in repo_data and repo_data.get('head'):
        log.info(f"Repo {owner}/{repo} has a detached head, potential transfer")
        return True, "head_detached"
    
    events_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/events"
    events = get_with_retry(events_url)
    
    if isinstance(events, list):
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        for event in events[:20]:
            if event.get('type') == 'MemberEvent' and event.get('action') == 'added':
                event_date = event.get('created_at')
                if event_date:
                    try:
                        event_dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
                        if event_dt > thirty_days_ago:
                            return True, "owner_added_recently"
                    except ValueError:
                        pass
    
    return False, None


def check_massive_pr(pr: dict, owner: str, repo: str) -> Optional[dict]:
    """Check if a PR is massive from an unknown contributor."""
    if not pr:
        return None
    
    user = pr.get('user', {})
    login = user.get('login', 'unknown')
    
    commits_url = pr.get('commits_url')
    if not commits_url:
        return None
    
    commits_data = get_with_retry(commits_url)
    if not commits_data or not isinstance(commits_data, list):
        return None
    
    contributor_commits = len(commits_data)
    
    additions = pr.get('additions', 0)
    if additions == 0:
        pr_details_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr.get('number')}"
        pr_detail = get_with_retry(pr_details_url)
        if pr_detail:
            additions = pr_detail.get('additions', 0)
    
    if additions > 1000 and contributor_commits < 5:
        return {
            "author": login,
            "pr_number": pr.get('number'),
            "additions": additions,
            "contributor_commits": contributor_commits,
            "title": pr.get('title', '')[:200],
            "url": pr.get('html_url', '')
        }
    
    return None


def get_commit_author_info(commits: list) -> dict:
    """Extract author information from commits."""
    author_commits = {}
    for commit in commits:
        if isinstance(commit, dict):
            author = commit.get('author', {})
            if author and isinstance(author, dict):
                login = author.get('login')
                if login:
                    author_commits[login] = author_commits.get(login, 0) + 1
    
    if not author_commits:
        if commits and isinstance(commits[0], dict):
            commit_obj = commits[0].get('commit', {})
            author = commit_obj.get('author', {})
            if author:
                name = author.get('name', 'unknown')
                author_commits[name] = len(commits)
    
    return author_commits


def detect_high_velocity_new_contributor(commits: list, velocity: float) -> tuple:
    """Detect high commit velocity from new contributors."""
    if velocity < 50:
        return False, None, None
    
    author_commits = get_commit_author_info(commits)
    
    for author, count in author_commits.items():
        if count > velocity * 0.8:
            return True, author, count
    
    return False, None, None


def analyze_repo(server_id: str, repo_url: str) -> tuple:
    """Analyze a single GitHub repository."""
    parsed = parse_github_url(repo_url)
    if not parsed:
        log.warning(f"Could not parse repo URL: {repo_url}")
        return None, []
    
    owner, repo = parsed
    threats = []
    
    log.info(f"Analyzing {owner}/{repo}...")
    
    commits, commit_velocity = get_recent_commits(owner, repo)
    
    contributors = get_contributors(owner, repo)
    
    prs = get_merged_prs(owner, repo)
    
    is_transferred, transfer_reason = check_repo_transfer(owner, repo)
    
    high_velocity, new_author, new_author_commits = detect_high_velocity_new_contributor(commits, commit_velocity)
    
    contributor_count = len(contributors) if isinstance(contributors, list) else 0
    
    contributor_churn = 0
    if isinstance(contributors, list):
        for c in contributors:
            if isinstance(c, dict):
                contributions = c.get('contributions', 0)
                if contributions > 0:
                    contributor_churn = max(contributor_churn, contributions // 10)
    
    velocity_data = {
        "server_id": server_id,
        "repo_url": repo_url,
        "commit_velocity": round(commit_velocity, 2),
        "contributor_churn": contributor_churn,
        "contributor_count": contributor_count,
        "last_suspicious_commit": None,
        "analyzed_at": datetime.now(timezone.utc).isoformat()
    }
    
    if high_velocity and new_author:
        velocity_data["last_suspicious_commit"] = new_author
        threat = {
            "server_id": server_id,
            "threat_type": "high_velocity_new_contributor",
            "evidence": f"Commit velocity {commit_velocity:.1f}/week with {new_author_commits} commits from new contributor '{new_author}'",
            "severity": "HIGH",
            "reported_at": datetime.now(timezone.utc).isoformat()
        }
        threats.append(threat)
        log.warning(f"HIGH threat detected for {server_id}: high velocity from new contributor {new_author}")
    
    if isinstance(prs, list):
        for pr in prs[:5]:
            massive = check_massive_pr(pr, owner, repo)
            if massive:
                velocity_data["last_suspicious_commit"] = massive["author"]
                threat = {
                    "server_id": server_id,
                    "threat_type": "massive_pr_unknown_contributor",
                    "evidence": f"PR #{massive['pr_number']} with {massive['additions']} additions from '{massive['author']}' (only {massive['contributor_commits']} prior commits)",
                    "severity": "CRITICAL",
                    "reported_at": datetime.now(timezone.utc).isoformat()
                }
                threats.append(threat)
                log.critical(f"CRITICAL threat for {server_id}: massive PR #{massive['pr_number']} from {massive['author']}")
                break
    
    if is_transferred:
        threat = {
            "server_id": server_id,
            "threat_type": "recent_repo_transfer",
            "evidence": f"Repository transferred to new owner ({transfer_reason or 'detected'})",
            "severity": "CRITICAL",
            "reported_at": datetime.now(timezone.utc).isoformat()
        }
        threats.append(threat)
        log.critical(f"CRITICAL threat for {server_id}: recent repo transfer detected")
    
    return velocity_data, threats


def get_github_servers() -> list:
    """Fetch MCP servers with GitHub URLs from registry."""
    sql = """
        SELECT server_id, url, name
        FROM mcp_server_registry
        WHERE url LIKE '%github.com%'
        AND (verdict IS NULL OR verdict != 'EXEMPT')
    """
    return ws_query(sql)


def ensure_tables() -> bool:
    """Create required tables if they don't exist."""
    velocity_table = """
        CREATE TABLE IF NOT EXISTS github_velocity (
            id BIGINT PRIMARY KEY,
            server_id VARCHAR NOT NULL,
            repo_url VARCHAR,
            commit_velocity REAL,
            contributor_churn INTEGER,
            contributor_count INTEGER,
            last_suspicious_commit VARCHAR,
            analyzed_at TIMESTAMPTZ DEFAULT now()
        )
    """
    try:
        response = requests.post(EXECUTE_URL, json={"sql": velocity_table}, timeout=30)
        if response.status_code == 200:
            log.info("Ensured github_velocity table exists")
            return True
        log.error(f"Failed to create velocity table: {response.text}")
        return False
    except requests.exceptions.RequestException as e:
        log.error(f"Error ensuring tables: {e}")
        return False


def remove_pid_file():
    """Clean up PID file on exit."""
    global _shutdown_flag
    _shutdown_flag = True
    try:
        if os.path.exists(_pid_file):
            os.remove(_pid_file)
            log.info("PID file removed")
    except OSError as e:
        log.warning(f"Failed to remove PID file: {e}")


def run():
    """Main run loop for the GitHub velocity analyser."""
    import signal
    import sys
    
    signal.signal(signal.SIGINT, lambda s, f: remove_pid_file())
    signal.signal(signal.SIGTERM, lambda s, f: remove_pid_file())
    
    log.info(f"Starting {SERVICE_NAME}...")
    
    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    log.info(f"GitHub token available: {'Yes' if GITHUB_TOKEN else 'No (using unauthenticated requests)'}")
    
    ensure_tables()
    
    send_heartbeat()
    
    try:
        while not _shutdown_flag:
            try:
                servers = get_github_servers()
                log.info(f"Found {len(servers)} GitHub repositories to analyze")
                
                for server in servers:
                    if _shutdown_flag:
                        break
                    
                    server_id = server.get('server_id')
                    repo_url = server.get('url')
                    
                    if not server_id or not repo_url:
                        continue
                    
                    velocity_data, threats = analyze_repo(server_id, repo_url)
                    
                    if velocity_data:
                        ws_write("github_velocity", velocity_data)
                    
                    for threat in threats:
                        ws_write("mcp_threat_associations", threat)
                    
                    with _state_lock:
                        _last_run = datetime.now(timezone.utc)
                
                log.info(f"Cycle complete. Next cycle in {CYCLE_INTERVAL}s")
                
            except Exception as e:
                log.error(f"Error in analysis cycle: {e}", exc_info=True)
            
            send_heartbeat()
            
            for _ in range(CYCLE_INTERVAL):
                if _shutdown_flag:
                    break
                time.sleep(1)
    
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


def heartbeat_loop():
    """Background heartbeat thread."""
    import threading
    def _heartbeat():
        while not _shutdown_flag:
            send_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)
    threading.Thread(target=_heartbeat, daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run()