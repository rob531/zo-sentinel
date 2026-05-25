import os
import re
import time
import json
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import requests
import uvicorn
from fastapi import FastAPI

SERVICE_NAME = "mcp_project_canonicalizer"
SERVICE_PORT = 8786
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
POLL_SECS = 3600

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Query failed: {sql[:100]} - {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed for table {table}: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {sql[:100]} - {e}")
        return False

def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        if os.path.exists(f"/proc/{old_pid}"):
            log.warning(f"Instance already running with PID {old_pid}")
            return False
        else:
            log.info("Stale PID file removed")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    exit(0)

def send_heartbeat():
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def create_tables() -> bool:
    create_canonical = """
    CREATE TABLE IF NOT EXISTS mcp_project_canonical (
        canonical_id VARCHAR PRIMARY KEY,
        canonical_name VARCHAR NOT NULL,
        project_type VARCHAR NOT NULL,
        primary_identifier VARCHAR NOT NULL,
        npm_package VARCHAR,
        github_repo VARCHAR,
        pypi_package VARCHAR,
        display_name VARCHAR,
        description VARCHAR,
        member_count INTEGER DEFAULT 0,
        first_seen_at VARCHAR,
        last_updated_at VARCHAR
    )
    """
    
    create_members = """
    CREATE TABLE IF NOT EXISTS mcp_project_members (
        member_id INTEGER PRIMARY KEY,
        canonical_id VARCHAR NOT NULL,
        server_id VARCHAR NOT NULL,
        member_name VARCHAR NOT NULL,
        member_type VARCHAR NOT NULL,
        url VARCHAR,
        added_at VARCHAR,
        UNIQUE(canonical_id, server_id)
    )
    """
    
    if not ws_execute(create_canonical):
        return False
    if not ws_execute(create_members):
        return False
    
    log.info("Tables created successfully")
    return True

def extract_npm_package(url: str, name: str) -> Optional[str]:
    if not url:
        return None
    if "npmjs.com" in url or "npmjs.org" in url:
        match = re.search(r'/@?([^/]+)/([^/]+)', url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        match = re.search(r'/package/([^/]+)', url)
        if match:
            return match.group(1)
    if "npm" in name.lower() and name.lower() not in ["npm", "npx"]:
        return name.lower().replace(" ", "-")
    return None

def extract_github_repo(url: str) -> Optional[str]:
    if not url:
        return None
    patterns = [
        r'github\.com[/:]([^/]+)/([^/\s]+?)(?:\.git)?(?:/|$)',
        r'://([^/]+)/([^/]+)/([^/\s]+?)(?:\.git)?(?:/|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return f"{match.group(1)}/{match.group(2)}".lower()
    return None

def extract_pypi_package(url: str, name: str) -> Optional[str]:
    if not url:
        return None
    if "pypi.org" in url or "pypi.python.org" in url:
        match = re.search(r'/project/([^/]+)', url)
        if match:
            return match.group(1).lower().replace('%2F', '/')
    if "pypi" in name.lower():
        return name.lower().replace(" ", "-")
    return None

def normalize_package_name(name: str) -> str:
    if not name:
        return ""
    return re.sub(r'[^a-z0-9\-_/]', '', name.lower())

def compute_canonical_id(identifiers: Dict[str, str]) -> str:
    key_parts = []
    for key in sorted(identifiers.keys()):
        if identifiers[key]:
            key_parts.append(f"{key}:{identifiers[key]}")
    key_string = "|".join(key_parts)
    return hashlib.sha256(key_string.encode()).hexdigest()[:16]

def get_all_servers() -> List[Dict[str, Any]]:
    result = ws_query("SELECT server_id, name, url, description FROM mcp_server_registry")
    return result.get("rows", [])

def build_canonical_projects(servers: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    projects = {}
    
    for server in servers:
        server_id = server.get("server_id", "")
        name = server.get("name", "")
        url = server.get("url", "")
        description = server.get("description", "") or ""
        
        npm_pkg = extract_npm_package(url, name)
        github_repo = extract_github_repo(url)
        pypi_pkg = extract_pypi_package(url, name)
        
        identifiers = {
            "npm": npm_pkg,
            "github": github_repo,
            "pypi": pypi_pkg
        }
        
        has_any_identifier = any(v for v in identifiers.values())
        
        if not has_any_identifier:
            canonical_id = f"local_{normalize_package_name(name)[:32]}"
            identifiers = {"local": normalize_package_name(name)}
        else:
            canonical_id = compute_canonical_id(identifiers)
        
        if canonical_id not in projects:
            display_name = npm_pkg or github_repo or pypi_pkg or name
            project_type = "npm" if npm_pkg else "github" if github_repo else "pypi" if pypi_pkg else "local"
            
            projects[canonical_id] = {
                "canonical_id": canonical_id,
                "canonical_name": normalize_package_name(display_name),
                "project_type": project_type,
                "primary_identifier": npm_pkg or github_repo or pypi_pkg or name,
                "npm_package": npm_pkg,
                "github_repo": github_repo,
                "pypi_package": pypi_pkg,
                "display_name": display_name,
                "description": description[:500] if description else "",
                "members": []
            }
        
        member_type = "npm" if npm_pkg else "github" if github_repo else "pypi" if pypi_pkg else "local"
        projects[canonical_id]["members"].append({
            "server_id": server_id,
            "member_name": name,
            "member_type": member_type,
            "url": url
        })
    
    return projects

def sync_canonical_projects(projects: Dict[str, Dict[str, Any]]) -> int:
    now = datetime.utcnow().isoformat()
    canonical_rows = []
    member_rows = []
    
    for proj in projects.values():
        member_count = len(proj["members"])
        
        existing = ws_query(f"SELECT member_count FROM mcp_project_canonical WHERE canonical_id = '{proj['canonical_id']}'")
        first_seen = now
        if existing.get("rows") and len(existing["rows"]) > 0:
            old_count = existing["rows"][0].get("member_count", 0)
            if old_count > 0:
                first_seen = now
            else:
                old_row = ws_query(f"SELECT first_seen_at FROM mcp_project_canonical WHERE canonical_id = '{proj['canonical_id']}'")
                if old_row.get("rows"):
                    first_seen = old_row["rows"][0].get("first_seen_at", now)
        
        canonical_rows.append({
            "canonical_id": proj["canonical_id"],
            "canonical_name": proj["canonical_name"],
            "project_type": proj["project_type"],
            "primary_identifier": proj["primary_identifier"],
            "npm_package": proj["npm_package"],
            "github_repo": proj["github_repo"],
            "pypi_package": proj["pypi_package"],
            "display_name": proj["display_name"],
            "description": proj["description"],
            "member_count": member_count,
            "first_seen_at": first_seen,
            "last_updated_at": now
        })
        
        for member in proj["members"]:
            member_rows.append({
                "canonical_id": proj["canonical_id"],
                "server_id": member["server_id"],
                "member_name": member["member_name"],
                "member_type": member["member_type"],
                "url": member["url"],
                "added_at": now
            })
    
    if canonical_rows:
        ws_write("mcp_project_canonical", canonical_rows)
    
    if member_rows:
        ws_execute("DELETE FROM mcp_project_members")
        ws_write("mcp_project_members", member_rows)
    
    return len(canonical_rows)

def identify_supply_chain_forks() -> List[Dict[str, Any]]:
    query = """
    SELECT 
        canonical_id,
        canonical_name,
        project_type,
        primary_identifier,
        member_count,
        npm_package,
        github_repo,
        pypi_package
    FROM mcp_project_canonical
    WHERE member_count > 1
    ORDER BY member_count DESC
    LIMIT 100
    """
    result = ws_query(query)
    return result.get("rows", [])

def compute_supply_chain_risk() -> None:
    forks = identify_supply_chain_forks()
    
    for fork in forks:
        log.info(f"Supply chain fork detected: {fork['canonical_name']} with {fork['member_count']} members")
        
        members_result = ws_query(f"""
            SELECT server_id, member_name, member_type, url
            FROM mcp_project_members
            WHERE canonical_id = '{fork['canonical_id']}'
        """)
        
        for member in members_result.get("rows", []):
            log.info(f"  - {member['member_type']}: {member['member_name']} ({member['url']})")

def heartbeat_loop():
    import threading
    def heartbeat_thread():
        while True:
            send_heartbeat()
            time.sleep(POLL_SECS)
    t = threading.Thread(target=heartbeat_thread, daemon=True)
    t.start()

def run():
    log.info(f"Starting {SERVICE_NAME}...")
    
    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        return
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not create_tables():
        log.error("Failed to create tables. Exiting.")
        remove_pid_file()
        return
    
    log.info("Starting heartbeat thread")
    heartbeat_loop()
    
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            log.info(f"Starting canonicalization cycle {cycle_count}")
            
            servers = get_all_servers()
            log.info(f"Found {len(servers)} MCP servers to canonicalize")
            
            if servers:
                projects = build_canonical_projects(servers)
                log.info(f"Built {len(projects)} canonical projects")
                
                synced = sync_canonical_projects(projects)
                log.info(f"Synced {synced} canonical projects to database")
                
                compute_supply_chain_risk()
            else:
                log.warning("No servers found in registry")
            
            log.info(f"Cycle {cycle_count} completed. Sleeping for {POLL_SECS} seconds.")
            
        except Exception as e:
            log.error(f"Error in canonicalization cycle: {e}", exc_info=True)
        
        time.sleep(POLL_SECS)

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.get("/projects")
def get_projects():
    result = ws_query("SELECT * FROM mcp_project_canonical ORDER BY member_count DESC")
    return result

@app.get("/projects/{canonical_id}")
def get_project(canonical_id: str):
    project_result = ws_query(f"SELECT * FROM mcp_project_canonical WHERE canonical_id = '{canonical_id}'")
    members_result = ws_query(f"SELECT * FROM mcp_project_members WHERE canonical_id = '{canonical_id}'")
    
    if not project_result.get("rows"):
        return {"error": "Project not found"}, 404
    
    return {
        "project": project_result["rows"][0],
        "members": members_result.get("rows", [])
    }

@app.get("/forks")
def get_supply_chain_forks():
    result = ws_query("""
        SELECT * FROM mcp_project_canonical 
        WHERE member_count > 1 
        ORDER BY member_count DESC
    """)
    return result

@app.post("/sync")
def trigger_sync():
    servers = get_all_servers()
    projects = build_canonical_projects(servers)
    synced = sync_canonical_projects(projects)
    return {"synced_projects": synced, "total_servers": len(servers)}

def main():
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    main()