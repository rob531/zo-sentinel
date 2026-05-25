#!/usr/bin/env python3
"""
npm_metadata_backfiller.py
Long-running daemon that enriches npm registry entries with metadata from the npm registry API.
"""

import time
import json
import hashlib
import datetime
import threading
import requests
from typing import Dict, Any, Optional, List

# Configuration
POLL_SECS = 600
PORT = 8772
WRITE_SERVICE_URL = f"http://127.0.0.1:{PORT}/write"
QUERY_SERVICE_URL = f"http://127.0.0.1:{PORT}/query"
PID_FILE = "/tmp/npm_metadata_backfiller.pid"
USER_AGENT = "zo-sentinel/1.0"
INTER_FETCH_DELAY = 0.25  # 250ms polite delay

# HTTP session with timeout
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
HTTP_TIMEOUT = 8


def check_single_instance(pid_file: str) -> bool:
    """Ensure only one instance runs."""
    import os
    pid = os.getpid()
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            print(f"[FATAL] Another instance running with PID {existing_pid}")
            return False
        except OSError:
            pass
    with open(pid_file, 'w') as f:
        f.write(str(pid))
    return True


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service."""
    try:
        import urllib.request
        data = json.dumps({"table": table, "rows": rows, "wait": True}).encode('utf-8')
        req = urllib.request.Request(
            WRITE_SERVICE_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get('ok', False)
    except Exception as e:
        print(f"[ERROR] ws_write failed for {table}: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service (read-only)."""
    try:
        import urllib.request
        data = json.dumps({"sql": sql}).encode('utf-8')
        req = urllib.request.Request(
            QUERY_SERVICE_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get('rows', [])
    except Exception as e:
        print(f"[ERROR] ws_query failed: {e}")
        return []


def send_heartbeat():
    """Update service health timestamp."""
    ws_write("service_health", {
        "service": "npm_metadata_backfiller",
        "last_heartbeat": datetime.datetime.now(datetime.timezone.utc).isoformat()
    })


def fetch_npm_metadata(package_name: str) -> Optional[Dict[str, Any]]:
    """Fetch metadata for an npm package."""
    url = f"https://registry.npmjs.org/{package_name}/latest"
    try:
        response = session.get(url, timeout=HTTP_TIMEOUT)
        if response.status_code == 404:
            return {"npm_404": True}
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        print(f"[WARN] Timeout fetching {package_name}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Error fetching {package_name}: {e}")
        return None


def parse_npm_metadata(data: Dict[str, Any], package_name: str) -> Dict[str, Any]:
    """Parse relevant fields from npm registry response."""
    parsed = {}

    # Version
    if "version" in data:
        parsed["version"] = data["version"]

    # Description
    if "description" in data:
        parsed["description"] = data["description"]

    # Time created
    if "time" in data and isinstance(data["time"], dict):
        if "created" in data["time"]:
            parsed["time_created"] = data["time"]["created"]
        if "modified" in data["time"]:
            parsed["time_modified"] = data["time"]["modified"]

    # Dependencies count
    if "dependencies" in data and isinstance(data["dependencies"], dict):
        parsed["dependencies_count"] = len(data["dependencies"])

    # Repository URL
    if "repository" in data:
        repo = data["repository"]
        if isinstance(repo, dict) and "url" in repo:
            parsed["repository_url"] = repo["url"]
        elif isinstance(repo, str):
            parsed["repository_url"] = repo
        elif isinstance(repo, dict) and "homepage" in repo:
            parsed["repository_url"] = repo["homepage"]

    # Author name
    if "author" in data:
        author = data["author"]
        if isinstance(author, dict) and "name" in author:
            parsed["author_name"] = author["name"]
        elif isinstance(author, str):
            parsed["author_name"] = author

    # Maintainers count
    if "maintainers" in data and isinstance(data["maintainers"], list):
        parsed["maintainers_count"] = len(data["maintainers"])

    # Homepage
    if "homepage" in data:
        parsed["homepage"] = data["homepage"]

    return parsed


def merge_metadata(existing: Optional[Dict[str, Any]], new_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Merge new metadata fields into existing, preserving our set keys."""
    if not existing or existing == '{}':
        existing = {}
    elif isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except (json.JSONDecodeError, TypeError):
            existing = {}

    # New fields overwrite existing ones
    merged = dict(existing)
    merged.update(new_fields)
    return merged


def build_facts_records(server_id: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build fact records for each metadata field."""
    facts = []
    for field, value in metadata.items():
        fact_type = f"npm_{field}"
        fact_value = str(value) if value is not None else ""
        facts.append({
            "server_id": server_id,
            "fact_type": fact_type,
            "fact_value": fact_value,
            "fact_source": "npm_registry_api",
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })
    return facts


def process_server(server: Dict[str, Any], errors: Dict[str, int]) -> bool:
    """Process a single server record."""
    server_id = server["server_id"]
    name = server["name"]
    
    print(f"[INFO] Processing {server_id}: {name}")

    # Fetch from npm
    raw_data = fetch_npm_metadata(name)
    
    if raw_data is None:
        errors["npm_error_count"] = errors.get("npm_error_count", 0) + 1
        return False

    # Check for 404
    if raw_data.get("npm_404"):
        print(f"[INFO] {name} not found on npm registry")
        # Build minimal metadata with 404 flag
        new_fields = {"npm_404": True}
        # Get existing metadata
        existing = server.get("metadata")
        merged = merge_metadata(existing, new_fields)
        
        # Update server record
        ws_write("mcp_server_registry", {
            "server_id": server_id,
            "metadata": json.dumps(merged),
            "last_scanned": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scan_count": (server.get("scan_count") or 0) + 1
        })
        return True

    # Parse metadata
    parsed = parse_npm_metadata(raw_data, name)
    if not parsed:
        print(f"[WARN] No parseable metadata for {name}")
        errors["npm_error_count"] = errors.get("npm_error_count", 0) + 1
        return False

    # Merge with existing
    existing = server.get("metadata")
    merged = merge_metadata(existing, parsed)

    # Update server registry
    ws_write("mcp_server_registry", {
        "server_id": server_id,
        "metadata": json.dumps(merged),
        "last_scanned": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scan_count": (server.get("scan_count") or 0) + 1
    })

    # Write facts
    facts = build_facts_records(server_id, parsed)
    for fact in facts:
        ws_write("mcp_registry_facts", fact)

    print(f"[INFO] Updated {name} with fields: {list(parsed.keys())}")
    return True


def fetch_servers_needing_scan() -> List[Dict[str, Any]]:
    """Query for servers that need metadata updates."""
    sql = """
    SELECT server_id, name, metadata, scan_count, last_scanned
    FROM mcp_server_registry
    WHERE registry_source = 'npm'
    AND (
        metadata IS NULL 
        OR metadata = '{}' 
        OR metadata NOT LIKE '%"version":%'
        OR last_scanned IS NULL 
        OR last_scanned < now() - INTERVAL 14 DAY
    )
    LIMIT 30
    """
    return ws_query(sql)


def run():
    """Main daemon loop."""
    print("[INFO] Starting npm_metadata_backfiller daemon")
    
    if not check_single_instance(PID_FILE):
        return

    errors: Dict[str, int] = {}
    consecutive_errors = 0

    while True:
        try:
            print(f"[INFO] Scanning for npm servers needing metadata...")
            
            servers = fetch_servers_needing_scan()
            
            if not servers:
                print("[INFO] No servers need metadata updates")
            else:
                print(f"[INFO] Found {len(servers)} servers to process")
                for server in servers:
                    success = process_server(server, errors)
                    if not success:
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0
                    
                    # Polite delay between fetches
                    time.sleep(INTER_FETCH_DELAY)

            # Log error count
            if errors:
                for key, val in errors.items():
                    print(f"[STATS] {key}: {val}")

        except Exception as e:
            print(f"[ERROR] Main loop exception: {e}")
            consecutive_errors += 1

        # Heartbeat every cycle
        send_heartbeat()

        # Exit on too many consecutive errors
        if consecutive_errors > 10:
            print(f"[FATAL] Too many consecutive errors ({consecutive_errors}), exiting")
            break

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()