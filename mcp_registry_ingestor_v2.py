#!/usr/bin/env python3
"""mcp_registry_ingestor_v2 - Ingests both remotes[] and packages[] from registry entries.

Reads both remotes[] (http/sse/ws transports) and packages[] (stdio transport)
from MCP registry entries and populates mcp_registry_facts with proper
transport classification. Idempotent via ON CONFLICT DO NOTHING.

After re-run, signal_bridge will have real transport info as backup for Signals 1 and 4.
"""

import sys
import time
import signal
import hashlib
import json
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List

# ─── Constants ────────────────────────────────────────────────────────────────
SERVICE_NAME = "mcp_registry_ingestor_v2"
PORT = 8791
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/var/log/zo_sentinel/{SERVICE_NAME}.log"
POLL_SECS = 3600  # 1 hour between full syncs

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

REGISTRY_SOURCES = [
    "https://smithery.ai/api/mcp",
    "https://catalog.smithery.ai/api/mcp",
]

# ─── Logging ─────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        import os
        os.makedirs("/var/log/zo_sentinel", exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ─── DB Helpers ───────────────────────────────────────────────────────────────
def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        r = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error to {table}: {e}", "ERROR")
        return False

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    payload = {"sql": sql}
    try:
        r = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"ws_query error: {e}", "ERROR")
        return None

def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        r = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}", "ERROR")
        return False

# ─── Single Instance Guard ────────────────────────────────────────────────────
def check_single_instance() -> bool:
    import os
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old = int(f.read().strip())
        try:
            os.kill(old, 0)
            log(f"Already running as PID {old}, exiting", "WARN")
            return False
        except OSError:
            log(f"Stale PID file found, taking over")
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True

def remove_pid_file():
    try:
        import os
        os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(sig, frame):
    log(f"Caught signal {sig}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

# ─── Table Setup ──────────────────────────────────────────────────────────────
def ensure_tables() -> bool:
    """Create mcp_registry_facts table if not exists."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_registry_facts (
        server_id       VARCHAR PRIMARY KEY,
        registry_source VARCHAR NOT NULL,
        package_name    VARCHAR,
        remote_name     VARCHAR,
        transport_type  VARCHAR NOT NULL,
        transport_url   VARCHAR,
        raw_packages    VARCHAR,
        raw_remotes     VARCHAR,
        ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return ws_execute(create_sql)

# ─── Registry Fetching ────────────────────────────────────────────────────────
def fetch_registry_source(url: str, timeout: int = 60) -> Optional[List[Dict[str, Any]]]:
    """Fetch MCP registry entries from a source URL."""
    try:
        headers = {
            "User-Agent": "ZO-Sentinel-Registry-Ingestor/2.0",
            "Accept": "application/json"
        }
        log(f"Fetching registry from {url}")
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        
        # Handle various response formats
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            # Some APIs wrap in {mcp: [...]} or {packages: [...]} or {data: [...]}
            for key in ["mcp", "packages", "data", "servers", "results"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Sometimes it's {mcpServers: {...}} format
            if "mcpServers" in data:
                # Convert {name: {...}} format to list of {name, ...}
                return [{"name": k, **v} for k, v in data["mcpServers"].items()]
        return None
    except Exception as e:
        log(f"Failed to fetch {url}: {e}", "ERROR")
        return None

# ─── Transport Classification ─────────────────────────────────────────────────
def classify_transport(entry: Dict[str, Any], is_remote: bool = False) -> tuple:
    """Classify transport type from entry metadata.
    
    Returns: (transport_type: str, transport_url: Optional[str])
    """
    if is_remote:
        # Remotes typically have http/sse/ws transports
        # Check transport field first
        transport = entry.get("transport", "")
        
        if not transport:
            # Check command/commandUrl for hints
            command = entry.get("command", "")
            url = entry.get("url", "")
            
            if "sse" in command.lower() or "sse" in url.lower():
                transport = "sse"
            elif "ws://" in url or "wss://" in url:
                transport = "ws"
            elif url.startswith("http"):
                transport = "http"
            else:
                transport = "http"  # Default for remotes
        
        return transport, entry.get("url", entry.get("command", ""))
    else:
        # Packages typically use stdio
        return "stdio", None

def extract_server_id(name: str, registry_source: str) -> str:
    """Generate consistent server_id from name and source."""
    # Use lowercase normalized name
    normalized = name.lower().strip()
    combined = f"{registry_source}:{normalized}"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]

# ─── Entry Processing ─────────────────────────────────────────────────────────
def process_entry(entry: Dict[str, Any], registry_source: str) -> List[Dict[str, Any]]:
    """Process a registry entry and extract facts for both packages and remotes.
    
    Returns list of facts dicts ready for mcp_registry_facts.
    """
    facts = []
    name = entry.get("name", "")
    
    if not name:
        return facts
    
    # Extract packages[] - stdio transports
    packages = entry.get("packages", [])
    if packages and isinstance(packages, list):
        for pkg in packages:
            pkg_name = pkg.get("name", name)
            transport_type, transport_url = classify_transport(pkg, is_remote=False)
            server_id = extract_server_id(pkg_name, registry_source)
            
            fact = {
                "server_id": server_id,
                "registry_source": registry_source,
                "package_name": pkg_name,
                "remote_name": None,
                "transport_type": transport_type,
                "transport_url": transport_url,
                "raw_packages": json.dumps([pkg]),
                "raw_remotes": "[]",
                "ingested_at": datetime.utcnow().isoformat()
            }
            facts.append(fact)
    
    # Extract remotes[] - http/sse/ws transports  
    remotes = entry.get("remotes", [])
    if remotes and isinstance(remotes, list):
        for remote in remotes:
            remote_name = remote.get("name", f"{name}-remote")
            transport_type, transport_url = classify_transport(remote, is_remote=True)
            server_id = extract_server_id(remote_name, registry_source)
            
            fact = {
                "server_id": server_id,
                "registry_source": registry_source,
                "package_name": None,
                "remote_name": remote_name,
                "transport_type": transport_type,
                "transport_url": transport_url,
                "raw_packages": "[]",
                "raw_remotes": json.dumps([remote]),
                "ingested_at": datetime.utcnow().isoformat()
            }
            facts.append(fact)
    
    # If neither packages nor remotes, try the entry itself (legacy format)
    if not packages and not remotes:
        transport_type, transport_url = classify_transport(entry, is_remote=False)
        server_id = extract_server_id(name, registry_source)
        
        fact = {
            "server_id": server_id,
            "registry_source": registry_source,
            "package_name": name,
            "remote_name": None,
            "transport_type": transport_type,
            "transport_url": transport_url,
            "raw_packages": json.dumps([entry]),
            "raw_remotes": "[]",
            "ingested_at": datetime.utcnow().isoformat()
        }
        facts.append(fact)
    
    return facts

# ─── Idempotent Write ─────────────────────────────────────────────────────────
def write_facts_idempotent(facts: List[Dict[str, Any]]) -> int:
    """Write facts to mcp_registry_facts using ON CONFLICT DO NOTHING.
    
    Returns count of successfully written rows.
    """
    if not facts:
        return 0
    
    # Use batch insert with ON CONFLICT DO NOTHING for idempotency
    # We do this via execute since write_service doesn't support ON CONFLICT directly
    sql = """
    INSERT INTO mcp_registry_facts (
        server_id, registry_source, package_name, remote_name,
        transport_type, transport_url, raw_packages, raw_remotes, ingested_at
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    ON CONFLICT (server_id) DO NOTHING
    """
    
    success_count = 0
    for fact in facts:
        params = (
            fact["server_id"],
            fact["registry_source"],
            fact["package_name"],
            fact["remote_name"],
            fact["transport_type"],
            fact["transport_url"],
            fact["raw_packages"],
            fact["raw_remotes"],
            fact["ingested_at"]
        )
        try:
            payload = {"sql": sql, "params": params}
            r = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
            if r.status_code == 200:
                success_count += 1
        except Exception as e:
            log(f"Failed to insert server_id {fact['server_id']}: {e}", "WARN")
    
    return success_count

# ─── Full Sync Cycle ──────────────────────────────────────────────────────────
def sync_registry_source(url: str) -> int:
    """Sync one registry source, return count of facts written."""
    entries = fetch_registry_source(url)
    if not entries:
        log(f"No entries from {url}")
        return 0
    
    all_facts = []
    for entry in entries:
        facts = process_entry(entry, url)
        all_facts.extend(facts)
    
    log(f"Processed {len(entries)} entries -> {len(all_facts)} facts from {url}")
    
    if all_facts:
        # Batch write in chunks of 100
        total_written = 0
        chunk_size = 100
        for i in range(0, len(all_facts), chunk_size):
            chunk = all_facts[i:i+chunk_size]
            written = write_facts_idempotent(chunk)
            total_written += written
        
        log(f"Wrote {total_written}/{len(all_facts)} facts from {url}")
        return total_written
    
    return 0

def full_sync() -> Dict[str, int]:
    """Run full sync across all registry sources.
    
    Returns dict of source_url -> count written.
    """
    results = {}
    for source_url in REGISTRY_SOURCES:
        count = sync_registry_source(source_url)
        results[source_url] = count
    return results

# ─── Heartbeat ────────────────────────────────────────────────────────────────
def send_heartbeat():
    """Send service heartbeat to service_health."""
    payload = {
        "table": "service_health",
        "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}],
        "wait": True
    }
    try:
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        log(f"Heartbeat failed: {e}", "WARN")

# ─── Stats Query ──────────────────────────────────────────────────────────────
def get_facts_stats() -> Dict[str, Any]:
    """Get current stats from mcp_registry_facts."""
    stats = {
        "total": 0,
        "by_transport": {},
        "by_source": {}
    }
    
    rows = ws_query("SELECT transport_type, registry_source, COUNT(*) as cnt FROM mcp_registry_facts GROUP BY transport_type, registry_source")
    if rows:
        for r in rows:
            stats["total"] += r["cnt"]
            transport = r["transport_type"]
            source = r["registry_source"]
            stats["by_transport"][transport] = stats["by_transport"].get(transport, 0) + r["cnt"]
            stats["by_source"][source] = stats["by_source"].get(source, 0) + r["cnt"]
    
    return stats

# ─── FastAPI App ─────────────────────────────────────────────────────────────
from fastapi import FastAPI
import uvicorn

app = FastAPI()
start_time = time.time()

@app.get("/health")
def health():
    uptime = time.time() - start_time
    return {"status": "ok", "service": SERVICE_NAME, "uptime_seconds": uptime}

@app.post("/sync")
def trigger_sync():
    """Manually trigger a full registry sync."""
    results = full_sync()
    stats = get_facts_stats()
    return {"status": "synced", "results": results, "stats": stats}

@app.get("/stats")
def get_stats():
    """Get current fact statistics."""
    return get_facts_stats()

def run():
    """Main daemon run loop."""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        sys.exit(1)
    
    log(f"Starting {SERVICE_NAME}")
    
    # Ensure tables exist
    if not ensure_tables():
        log("Failed to create tables, exiting", "ERROR")
        remove_pid_file()
        sys.exit(1)
    
    # Run initial sync
    log("Running initial full sync...")
    results = full_sync()
    log(f"Initial sync complete: {results}")
    
    send_heartbeat()
    
    # Main loop
    while True:
        try:
            time.sleep(POLL_SECS)
            
            # Periodic sync
            results = full_sync()
            log(f"Cycle sync complete: {results}")
            
            # Heartbeat
            send_heartbeat()
            
        except Exception as e:
            log(f"Main loop error: {e}", "ERROR")
            time.sleep(60)

def run_api():
    """Run FastAPI server only (for HTTP health/sync endpoints)."""
    uvicorn.run(app, host="127.0.0.1", port=PORT)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--daemon", action="store_true", help="Run as daemon with sync loop")
    parser.add_argument("--api", action="store_true", help="Run HTTP API server")
    parser.add_argument("--sync-once", action="store_true", help="Run single sync and exit")
    args = parser.parse_args()
    
    if args.sync_once:
        # Single sync run
        if not ensure_tables():
            log("Failed to create tables", "ERROR")
            sys.exit(1)
        results = full_sync()
        stats = get_facts_stats()
        log(f"Sync complete: {results}")
        log(f"Stats: {stats}")
    elif args.api:
        run_api()
    else:
        run()