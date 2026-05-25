#!/usr/bin/env python3
"""
discovery_npm_paginator.py
Long-running daemon that paginates npm search APIs to discover MCP-related packages.
"""
import json
import time
import signal
import os
import sys
import threading
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "discovery_npm_paginator"
STATE_DIR = Path("/home/workspace/zo_sentinel/state")
STATE_FILE = STATE_DIR / "npm_pagination_cursor.json"
LOCK_FILE = Path("/home/workspace/logs/discovery_npm_paginator.lock")
LOG_FILE = Path("/home/workspace/logs/discovery_npm_paginator.log")
CURSOR_FILE = Path("/tmp/discovery_npm_paginator.cursor")

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

POLL_SECS = 1800
FETCH_TIMEOUT_SECS = 10
FETCH_DELAY_MS = 0.5
HEARTBEAT_INTERVAL_SECS = 30
PAGE_SIZE = 100
USER_AGENT = "zo-sentinel/1.0 (mcp-trust-intelligence)"

QUERY_CONFIGS = [
    {
        "key": "keywords_modelcontextprotocol",
        "label": "keywords_modelcontextprotocol",
        "base_url": "https://registry.npmjs.com/-/v1/search",
        "params_template": {"text": "keywords:modelcontextprotocol", "size": PAGE_SIZE},
        "offset_param": "from",
    },
    {
        "key": "mcp-server",
        "label": "mcp-server",
        "base_url": "https://registry.npmjs.com/-/v1/search",
        "params_template": {"text": "mcp-server", "size": PAGE_SIZE},
        "offset_param": "from",
    },
]

stop_event = threading.Event()
heartbeat_thread = None


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_cursor():
    default = {
        "keywords_modelcontextprotocol": 0,
        "mcp-server": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        log(f"warn: failed to load cursor: {e}")
    return default


def save_cursor(cursor):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cursor, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"warn: failed to save cursor: {e}")


def load_pid_file(path):
    try:
        if path.exists():
            with open(path, "r") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return None


def write_pid_file(path, pid):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(str(pid))
    except Exception as e:
        log(f"warn: failed to write PID file: {e}")


def remove_pid_file(path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def check_single_instance():
    own_pid = os.getpid()
    existing_pid = load_pid_file(LOCK_FILE)
    if existing_pid and existing_pid != own_pid:
        try:
            os.kill(existing_pid, 0)
            log(f"error: another instance already running as PID {existing_pid}")
            sys.exit(1)
        except OSError:
            log(f"info: stale lockfile detected, PID {existing_pid} not running, reclaiming")
    write_pid_file(LOCK_FILE, own_pid)


def signal_handler(signum, frame):
    sig_name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(signum, str(signum))
    log(f"info: received {sig_name}, shutting down gracefully")
    stop_event.set()


def setup_signals():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def get_write_url():
    return WRITE_SERVICE_URL


def ws_write(table, rows):
    url = get_write_url()
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: ws_write failed for {table}: {e}")
        return None


def ws_query(sql):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: ws_query failed: {e}")
        return None


def get_table_columns(table_name):
    sql = f"SELECT column_name FROM information_schema.columns WHERE table_name='{table_name}' AND ordinal_position<=20 ORDER BY ordinal_position"
    result = ws_query(sql)
    if result and result.get("rows"):
        return [r["column_name"] for r in result["rows"]]
    return None


def verify_candidates_schema():
    cols = get_table_columns("mcp_discovery_candidates")
    if cols:
        log(f"info: mcp_discovery_candidates columns: {cols}")
        return True
    log("warn: could not verify mcp_discovery_candidates schema")
    return False


def send_heartbeat():
    url = get_write_url()
    payload = {
        "table": "service_health",
        "rows": {"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()},
        "wait": True,
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log(f"warn: heartbeat failed: {e}")


def heartbeat_loop():
    while not stop_event.is_set():
        stop_event.wait(timeout=HEARTBEAT_INTERVAL_SECS)
        if not stop_event.is_set():
            send_heartbeat()


def start_heartbeat():
    global heartbeat_thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()


def should_keep_package(pkg):
    keywords = pkg.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords_lower = [k.lower() for k in keywords]
    
    name = pkg.get("name", "") or ""
    name_lower = name.lower()
    description = pkg.get("description") or ""
    description_lower = description.lower()
    
    if any(k in keywords_lower for k in ["modelcontextprotocol", "model-context-protocol", "mcp"]):
        return True
    
    if name.startswith("mcp-") or name.startswith("@modelcontextprotocol/") or "-mcp-" in name_lower or name_lower.endswith("-mcp"):
        return True
    
    if "model context protocol" in description_lower or "mcp server" in description_lower:
        return True
    
    return False


def fetch_npm_page(query_config, offset):
    params = dict(query_config["params_template"])
    params[query_config["offset_param"]] = offset
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        resp = requests.get(query_config["base_url"], params=params, headers=headers, timeout=FETCH_TIMEOUT_SECS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"warn: fetch failed for {query_config['label']} offset {offset}: {e}")
        return None


def process_page(data, query_key):
    packages = data.get("objects", [])
    kept = []
    for obj in packages:
        pkg = obj.get("package", {})
        if should_keep_package(pkg):
            name = pkg.get("name", "")
            description = pkg.get("description") or ""
            npm_url = pkg.get("links", {}).get("npm") or f"https://www.npmjs.com/package/{name}"
            if not name:
                continue
            kept.append({
                "candidate_name": name,
                "candidate_url": npm_url,
                "candidate_description": description[:500],
                "discovered_in_directory": "npm_search",
                "discovered_status": "active",
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "promoted": False,
            })
    return kept


def write_candidates(candidates):
    if not candidates:
        return 0
    result = ws_write("mcp_discovery_candidates", candidates)
    if result:
        return len(candidates)
    return 0


def advance_cursor(cursor, query_key):
    new_cursor = dict(cursor)
    new_cursor[query_key] = cursor.get(query_key, 0) + PAGE_SIZE
    new_cursor["updated_at"] = datetime.now(timezone.utc).isoformat()
    return new_cursor


def reset_cursor_for_key(cursor, query_key):
    new_cursor = dict(cursor)
    new_cursor[query_key] = 0
    new_cursor["updated_at"] = datetime.now(timezone.utc).isoformat()
    return new_cursor


def get_next_query_key(cursor):
    keys = [cfg["key"] for cfg in QUERY_CONFIGS]
    counts = {k: cursor.get(k, 0) for k in keys}
    min_key = min(counts, key=counts.get)
    return min_key


def cycle():
    cursor = load_cursor()
    next_key = get_next_query_key(cursor)
    query_config = next(cfg for cfg in QUERY_CONFIGS if cfg["key"] == next_key)
    current_offset = cursor.get(next_key, 0)
    
    seen = 0
    kept = 0
    written = 0
    errors = 0
    
    data = fetch_npm_page(query_config, current_offset)
    
    if data is None:
        errors += 1
        log(f"cycle done query={query_config['label']} page={current_offset//PAGE_SIZE} seen={seen} kept={kept} written={written} errors={errors}")
        return
    
    objects = data.get("objects", [])
    seen = len(objects)
    
    total = data.get("total", 0)
    next_offset = current_offset + PAGE_SIZE
    
    candidates = process_page(data, next_key)
    kept = len(candidates)
    
    if candidates:
        written = write_candidates(candidates)
        time.sleep(FETCH_DELAY_MS)
    
    if seen > 0 and next_offset >= total:
        cursor = reset_cursor_for_key(cursor, next_key)
        log(f"info: query {query_config['label']} completed (offset {current_offset} >= total {total}), resetting cursor")
    else:
        cursor = advance_cursor(cursor, next_key)
    
    save_cursor(cursor)
    
    page_num = current_offset // PAGE_SIZE
    log(f"cycle done query={query_config['label']} page={page_num} seen={seen} kept={kept} written={written} errors={errors}")


def run():
    log(f"info: starting {SERVICE_NAME}")
    check_single_instance()
    setup_signals()
    start_heartbeat()
    
    verify_candidates_schema()
    
    try:
        while not stop_event.is_set():
            cycle()
            stop_event.wait(timeout=POLL_SECS)
    except Exception as e:
        log(f"error: main loop exception: {e}")
    finally:
        remove_pid_file(LOCK_FILE)
        log(f"info: {SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()