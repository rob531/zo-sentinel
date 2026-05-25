import hashlib
import time
import requests
import logging
import os
import sys
import signal
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, List, Any

SERVICE_NAME = "mcp_discovery_feeder"
SERVICE_PORT = 8797
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
POLL_SECS = 1800

NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
NPM_KEYWORD = "mcp-server"
NPM_RATE_LIMIT = 60
NPM_DELAY_BETWEEN_CALLS = 60.0 / NPM_RATE_LIMIT

GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOPIC = "mcp-server"
GITHUB_PER_PAGE = 100
GITHUB_MAX_PAGES = 5

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)

PID_FILE = Path(f"/tmp/{SERVICE_NAME}.pid")


def check_single_instance() -> bool:
    if PID_FILE.exists():
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            logger.warning(f"Another instance already running with PID {old_pid}")
            return False
        except (ValueError, ProcessLookupError, OSError):
            PID_FILE.unlink()
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    if PID_FILE.exists():
        PID_FILE.unlink()


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def compute_server_id(name: str, url: str) -> str:
    combined = f"{name.lower().strip()}|{url.lower().strip()}"
    return hashlib.blake2s(combined.encode('utf-8'), digest_size=16).hexdigest()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        logger.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(rows: List[Dict[str, Any]], table: str) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=60
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Execute failed: {sql[:100]}... Error: {e}")
        return False


def send_heartbeat():
    ws_write([{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}], "service_health")


def get_existing_server_ids() -> Set[str]:
    sql = "SELECT server_id FROM mcp_server_registry"
    rows = ws_query(sql)
    return {row['server_id'] for row in rows if row.get('server_id')}


def get_scored_server_ids() -> Set[str]:
    sql = "SELECT server_id FROM mcp_server_registry WHERE trust_score IS NOT NULL"
    rows = ws_query(sql)
    return {row['server_id'] for row in rows if row.get('server_id')}


def emit_mesh_event(candidate_count: int, details: Dict[str, Any]):
    event = {
        "event_type": "discovery_batch",
        "candidate_count": candidate_count,
        "details": details,
        "created_at": datetime.utcnow().isoformat()
    }
    ws_write([event], "mesh_events")
    logger.info(f"Emitted mesh_event: discovery_batch with {candidate_count} candidates")


def discover_npm_packages(existing_ids: Set[str], scored_ids: Set[str]) -> List[Dict[str, Any]]:
    candidates = []
    seen_ids: Set[str] = set()
    
    logger.info("Starting npm discovery...")
    try:
        response = requests.get(
            NPM_SEARCH_URL,
            params={
                "text": f"keywords:{NPM_KEYWORD}",
                "size": 250
            },
            headers={"Accept": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        results = response.json().get("objects", [])
        
        for item in results:
            package_info = item.get("package", {})
            name = package_info.get("name", "")
            url = package_info.get("links", {}).get("npm", "")
            
            if not name:
                continue
            
            if name.startswith("@npm/") or name.startswith("@types/"):
                continue
            
            server_id = compute_server_id(name, url)
            
            if server_id in seen_ids:
                continue
            seen_ids.add(server_id)
            
            if server_id in existing_ids or server_id in scored_ids:
                continue
            
            description = (package_info.get("description") or "").replace("'", "''").replace("\\", "\\\\")
            version = package_info.get("version", "")
            
            candidate = {
                "server_id": server_id,
                "name": name,
                "url": url,
                "description": description,
                "version": version,
                "registry_source": "npm",
                "status": "PENDING_SCAN",
                "first_seen": datetime.utcnow().isoformat()
            }
            candidates.append(candidate)
            
            time.sleep(NPM_DELAY_BETWEEN_CALLS)
            
        logger.info(f"npm discovery: found {len(candidates)} new candidates")
        
    except requests.RequestException as e:
        logger.error(f"npm API request failed: {e}")
    
    return candidates


def discover_github_repos(existing_ids: Set[str], scored_ids: Set[str]) -> List[Dict[str, Any]]:
    candidates = []
    seen_ids: Set[str] = set()
    
    logger.info("Starting GitHub discovery...")
    headers = {"Accept": "application/vnd.github.v3+json"}
    
    try:
        page = 1
        while page <= GITHUB_MAX_PAGES:
            response = requests.get(
                f"{GITHUB_API_BASE}/search/repositories",
                params={
                    "q": f"topic:{GITHUB_TOPIC}",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": GITHUB_PER_PAGE,
                    "page": page
                },
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 403:
                logger.warning("GitHub API rate limited, stopping early")
                break
            
            response.raise_for_status()
            data = response.json()
            repos = data.get("items", [])
            
            if not repos:
                break
            
            for repo in repos:
                name = repo.get("full_name", "")
                url = repo.get("html_url", "")
                
                if not name:
                    continue
                
                server_id = compute_server_id(name, url)
                
                if server_id in seen_ids:
                    continue
                seen_ids.add(server_id)
                
                if server_id in existing_ids or server_id in scored_ids:
                    continue
                
                description = (repo.get("description") or "").replace("'", "''").replace("\\", "\\\\")
                stars = repo.get("stargazers_count", 0)
                
                candidate = {
                    "server_id": server_id,
                    "name": name,
                    "url": url,
                    "description": description,
                    "stars": stars,
                    "registry_source": "github",
                    "status": "PENDING_SCAN",
                    "first_seen": datetime.utcnow().isoformat()
                }
                candidates.append(candidate)
            
            page += 1
            time.sleep(1.2)
            
        logger.info(f"GitHub discovery: found {len(candidates)} new candidates")
        
    except requests.RequestException as e:
        logger.error(f"GitHub API request failed: {e}")
    
    return candidates


def write_candidates_to_registry(candidates: List[Dict[str, Any]]) -> int:
    if not candidates:
        return 0
    
    count = 0
    for candidate in candidates:
        name = candidate["name"].replace("'", "''")
        url = (candidate.get("url") or "").replace("'", "''")
        description = (candidate.get("description") or "").replace("'", "''")
        version = (candidate.get("version") or "").replace("'", "''")
        stars = candidate.get("stars") or 0
        
        sql = f"""
        INSERT INTO mcp_server_registry 
        (server_id, name, url, description, version, stars, registry_source, status, first_seen)
        VALUES (
            '{candidate["server_id"]}',
            '{name}',
            '{url}',
            '{description}',
            '{version}',
            {stars},
            '{candidate["registry_source"]}',
            'PENDING_SCAN',
            '{candidate["first_seen"]}'
        )
        ON CONFLICT (server_id) DO NOTHING
        """
        if ws_execute(sql):
            count += 1
    
    return count


def ensure_tables():
    ws_execute("""
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        url VARCHAR,
        description VARCHAR,
        version VARCHAR,
        stars INTEGER,
        registry_source VARCHAR,
        status VARCHAR,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        trust_score DOUBLE,
        verdict VARCHAR,
        trust_score_computed_at TIMESTAMP,
        scan_count INTEGER DEFAULT 0
    )
    """)
    
    ws_execute("""
    CREATE TABLE IF NOT EXISTS mesh_events (
        id INTEGER PRIMARY KEY,
        event_type VARCHAR,
        candidate_count INTEGER,
        details VARCHAR,
        created_at TIMESTAMP
    )
    """)


def cycle():
    logger.info("=" * 60)
    logger.info("Starting discovery cycle")
    start_time = datetime.utcnow()
    
    existing_ids = get_existing_server_ids()
    scored_ids = get_scored_server_ids()
    logger.info(f"Existing servers: {len(existing_ids)}, Already scored: {len(scored_ids)}")
    
    npm_candidates = discover_npm_packages(existing_ids, scored_ids)
    
    github_candidates = discover_github_repos(existing_ids, scored_ids)
    
    all_candidates = npm_candidates + github_candidates
    seen: Set[str] = set()
    unique_candidates = []
    for c in all_candidates:
        if c["server_id"] not in seen:
            seen.add(c["server_id"])
            unique_candidates.append(c)
    
    logger.info(f"Total unique candidates after cross-source dedup: {len(unique_candidates)}")
    
    written = write_candidates_to_registry(unique_candidates)
    logger.info(f"Wrote {written} new candidates to registry")
    
    cycle_duration = (datetime.utcnow() - start_time).total_seconds()
    emit_mesh_event(written, {
        "npm_count": len(npm_candidates),
        "github_count": len(github_candidates),
        "cycle_duration_seconds": cycle_duration,
        "timestamp": datetime.utcnow().isoformat()
    })
    
    send_heartbeat()
    
    logger.info(f"Discovery cycle complete in {cycle_duration:.1f}s")
    logger.info("=" * 60)


def run():
    if not check_single_instance():
        logger.error("Cannot start: another instance is running")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"Starting {SERVICE_NAME} daemon")
    ensure_tables()
    
    try:
        while True:
            cycle()
            logger.info(f"Sleeping for {POLL_SECS} seconds until next cycle")
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down")
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()