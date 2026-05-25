#!/usr/bin/env python3
"""
registry_promoter_daemon.py  v2  (2026-04-27)

v1 (built 2026-04-27T13:47Z) hit 400 on every cycle because the directive
description named columns that don't exist in mcp_registry_facts.
This v2 uses the live schema:

  mcp_registry_facts columns:
    id, registry_name, version, description, status, published_at,
    is_latest, package_count, primary_registry, primary_identifier,
    raw_packages, server_id, first_seen, last_seen

  mcp_server_registry columns:
    server_id, name, registry_source, url, description, trust_score,
    verdict, verdict_reasoning, confidence, last_assessed,
    first_seen, last_seen, last_scanned, scan_count, risk_tier, metadata

Idempotent. Selects only server_ids missing from registry; INSERT
collisions handled by WriteService PK enforcement.

Log handler fix: drop StreamHandler. nohup pipes stdout to the same
log file the FileHandler writes to, which produced the every-line-doubled
pattern in v1.
"""
import os
import sys
import time
import json
import logging
import threading
import requests
from datetime import datetime, timezone

SERVICE_NAME = "registry_promoter_daemon"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
LOCK_FILE = "/home/workspace/logs/registry_promoter_daemon.lock"
LOG_FILE = "/home/workspace/logs/registry_promoter_daemon.log"
POLL_SECS = 300
HEARTBEAT_SECS = 30
BATCH_SIZE = 100

os.makedirs("/home/workspace/logs", exist_ok=True)

# v2: only FileHandler. nohup re-pipes stdout to the same file so a
# StreamHandler would double every line.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(SERVICE_NAME)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> dict:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Query failed: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows) -> dict:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Write failed for table {table}: {e}")
        return {"ok": False}


def send_heartbeat() -> None:
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": _utcnow_iso(),
            },
            "wait": True,
        }
        requests.post(WRITE_URL, json=payload, timeout=10)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def heartbeat_loop() -> None:
    while True:
        time.sleep(HEARTBEAT_SECS)
        send_heartbeat()


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                existing_pid = int(f.read().strip())
            if existing_pid != pid:
                try:
                    os.kill(existing_pid, 0)
                    log.error(f"Another instance running PID {existing_pid}")
                    return False
                except OSError:
                    log.warning(f"Stale lockfile from PID {existing_pid}; reclaiming")
        except (ValueError, IOError) as e:
            log.warning(f"Could not read lockfile: {e}")
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(pid))
        log.info(f"Acquired lockfile {LOCK_FILE} with PID {pid}")
        return True
    except IOError as e:
        log.error(f"Could not write lockfile: {e}")
        return False


def remove_pid_file() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception as e:
        log.error(f"Error removing lockfile: {e}")


# ── SQL using the LIVE mcp_registry_facts schema ───────────────────────────────────────────

_SAFE_ID = set("0123456789abcdefABCDEF-_")


def _safe_server_id(server_id: str) -> str | None:
    """server_ids in mcp_registry_facts are MD5-style hex hashes; reject anything
    with characters outside hex+dash+underscore so we can safely inline into SQL."""
    if not server_id or len(server_id) > 64:
        return None
    if not all(c in _SAFE_ID for c in server_id):
        return None
    return server_id


def get_unpromoted_server_ids() -> list:
    sql = f"""
        SELECT DISTINCT server_id
        FROM mcp_registry_facts
        WHERE server_id IS NOT NULL
          AND server_id NOT IN (SELECT server_id FROM mcp_server_registry WHERE server_id IS NOT NULL)
        LIMIT {BATCH_SIZE}
    """
    result = ws_query(sql)
    return [r.get("server_id") for r in result.get("rows", []) if r.get("server_id")]


def get_server_facts(server_id: str) -> dict | None:
    safe = _safe_server_id(server_id)
    if safe is None:
        log.warning(f"refused unsafe server_id: {server_id!r}")
        return None
    sql = f"""
        SELECT
            server_id,
            registry_name,
            primary_registry,
            primary_identifier,
            description,
            version,
            status,
            first_seen,
            last_seen
        FROM mcp_registry_facts
        WHERE server_id = '{safe}'
        ORDER BY last_seen DESC NULLS LAST
        LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    return rows[0] if rows else None


def _build_canonical(facts: dict) -> dict:
    """Map mcp_registry_facts row -> mcp_server_registry row."""
    pid = facts.get("primary_identifier") or ""
    name = pid or facts.get("registry_name") or facts.get("server_id") or "unknown"
    src = facts.get("primary_registry") or facts.get("registry_name") or "facts"

    # Best-effort URL reconstruction from registry + identifier
    url = ""
    if pid and src:
        s = src.lower()
        if s == "npm":
            url = f"https://www.npmjs.com/package/{pid}"
        elif s == "github":
            url = f"https://github.com/{pid}"
        elif s == "pypi":
            url = f"https://pypi.org/project/{pid}/"

    return {
        "server_id": facts.get("server_id"),
        "name": name[:255],
        "registry_source": src[:64] if src else "facts",
        "url": url,
        "description": (facts.get("description") or "")[:1000],
        "trust_score": 0.0,
        "verdict": "unknown",
        "verdict_reasoning": "",
        "confidence": 0.0,
        "last_assessed": None,
        "first_seen": facts.get("first_seen"),
        "last_seen": facts.get("last_seen"),
        "last_scanned": None,
        "scan_count": 0,
        "risk_tier": "unassessed",
        "metadata": json.dumps({
            "version": facts.get("version"),
            "status": facts.get("status"),
            "primary_identifier": pid,
        }),
    }


def promote_servers(server_ids: list) -> int:
    promoted = 0
    for sid in server_ids:
        facts = get_server_facts(sid)
        if not facts:
            continue
        row = _build_canonical(facts)
        result = ws_write("mcp_server_registry", row)
        # WriteService responses sometimes use {"ok": True} or {"status": "ok"};
        # accept either, fall back to absence-of-error.
        ok = bool(result.get("ok")) or result.get("status") == "ok" or "error" not in result
        if ok:
            promoted += 1
        else:
            log.error(f"Failed to promote {sid}: {result}")
    return promoted


def cycle() -> int:
    log.info("cycle start")
    server_ids = get_unpromoted_server_ids()
    if not server_ids:
        log.info("no servers pending promotion")
        return 0
    log.info(f"found {len(server_ids)} servers pending promotion")
    n = promote_servers(server_ids)
    log.info(f"cycle done: promoted={n}")
    return n


def run() -> None:
    log.info(f"=== {SERVICE_NAME} v2 starting ===")
    if not check_single_instance():
        log.error("lockfile in use, exiting")
        sys.exit(1)
    try:
        send_heartbeat()
        threading.Thread(target=heartbeat_loop, daemon=True).start()
        log.info("heartbeat thread started")
        while True:
            try:
                cycle()
            except Exception as e:
                log.error(f"cycle error: {e}")
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("interrupt; shutting down")
    finally:
        remove_pid_file()
        log.info("shutdown complete")


if __name__ == "__main__":
    run()