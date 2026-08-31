import logging
import os
import sys
import time
import signal
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

SERVICE_NAME = "mcp_definition_history_backfill"
SERVICE_PORT = 8786
PID_FILE = "/tmp/mcp_definition_history_backfill.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"
POLL_SECS = 60
BATCH_SIZE = 50

LOG = logging.getLogger(SERVICE_NAME)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error("ws_write failed for table %s: %s", table, e)
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    payload = {"sql": sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        LOG.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return None


def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {}
    }
    ws_write("service_health", [row])


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = int(f.read().strip())
        try:
            os.kill(existing, 0)
            LOG.error("Already running with PID %d, exiting.", existing)
            return False
        except OSError:
            LOG.warning("Stale PID file from %d, overwriting.", existing)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    LOG.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def compute_def_hash(definition_json: str) -> str:
    return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()[:16]


def ensure_tables() -> bool:
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_definition_history (
        server_id        VARCHAR NOT NULL,
        version          INTEGER NOT NULL,
        definition_hash  VARCHAR NOT NULL,
        definition_json  TEXT,
        tool_count       INTEGER,
        schema_summary   TEXT,
        captured_at      TIMESTAMPTZ NOT NULL,
        backfill_source  VARCHAR,
        PRIMARY KEY (server_id, version)
    );
    """
    if not ws_execute(create_sql):
        LOG.error("Failed to create mcp_definition_history table.")
        return False
    ws_execute("""
    CREATE TABLE IF NOT EXISTS mcp_definition_backfill_log (
        id              INTEGER PRIMARY KEY,
        server_id       VARCHAR,
        status          VARCHAR,
        rows_written    INTEGER,
        last_version    INTEGER,
        completed_at    TIMESTAMPTZ,
        error_message   TEXT
    );
    """)
    return True


def get_servers_needing_history() -> List[Dict[str, Any]]:
    sql = """
    SELECT
        r.server_id,
        r.name,
        r.url,
        r.description,
        r.tool_schema,
        r.verdict,
        r.trust_score,
        r.registry_source,
        r.first_seen,
        r.last_seen,
        COALESCE(
            (SELECT MAX(version) FROM mcp_definition_history h WHERE h.server_id = r.server_id),
            0
        ) AS current_version
    FROM mcp_server_registry r
    WHERE r.tool_schema IS NOT NULL
      AND r.tool_schema != ''
    ORDER BY r.last_seen DESC
    LIMIT 500;
    """
    rows = ws_query(sql)
    return rows if rows is not None else []


def get_current_tool_definition(server_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT tool_schema, description, name, url, registry_source
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    LIMIT 1;
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0]
    return None


def parse_tool_count(tool_schema: Any) -> int:
    if not tool_schema:
        return 0
    if isinstance(tool_schema, str):
        try:
            import json
            data = json.loads(tool_schema)
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict) and "tools" in data:
                return len(data["tools"])
            return 0
        except Exception:
            return 0
    if isinstance(tool_schema, list):
        return len(tool_schema)
    if isinstance(tool_schema, dict):
        if "tools" in tool_schema:
            return len(tool_schema["tools"])
        return 1
    return 0


def build_schema_summary(tool_schema: Any) -> str:
    if not tool_schema:
        return ""
    try:
        if isinstance(tool_schema, str):
            import json
            tool_schema = json.loads(tool_schema)
        if isinstance(tool_schema, list):
            names = [t.get("name", t.get("function", {}).get("name", "?")) for t in tool_schema]
            return f"tools:[{', '.join(names[:20])}]"
        if isinstance(tool_schema, dict) and "tools" in tool_schema:
            names = [t.get("name", t.get("function", {}).get("name", "?")) for t in tool_schema["tools"]]
            return f"tools:[{', '.join(names[:20])}]"
        return str(tool_schema)[:200]
    except Exception:
        return str(tool_schema)[:200] if tool_schema else ""


def compute_definition_hash(row: Dict[str, Any]) -> str:
    raw = f"{row.get('tool_schema', '')}:{row.get('description', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def backfill_server_history(server_id: str, current_version: int) -> int:
    current = get_current_tool_definition(server_id)
    if not current:
        return current_version

    tool_schema = current.get("tool_schema", "")
    def_hash = compute_definition_hash(current)
    tool_count = parse_tool_count(tool_schema)
    schema_summary = build_schema_summary(tool_schema)
    now = utc_now_iso()

    new_version = current_version + 1
    history_row = {
        "server_id": server_id,
        "version": new_version,
        "definition_hash": def_hash,
        "definition_json": str(tool_schema) if tool_schema else None,
        "tool_count": tool_count,
        "schema_summary": schema_summary,
        "captured_at": now,
        "backfill_source": "initial_backfill"
    }
    if ws_write("mcp_definition_history", [history_row]):
        return new_version
    return current_version


def log_backfill_batch(server_id: str, status: str, rows_written: int = 0,
                        last_version: int = 0, error_message: str = "") -> None:
    now = utc_now_iso()
    sql = f"""
    INSERT INTO mcp_definition_backfill_log (server_id, status, rows_written, last_version, completed_at, error_message)
    VALUES ('{server_id}', '{status}', {rows_written}, {last_version}, '{now}', {f"'{error_message}'" if error_message else 'NULL'});
    """
    ws_execute(sql)


def cycle() -> Dict[str, Any]:
    stats = {
        "processed": 0,
        "new_versions": 0,
        "errors": 0,
        "skipped": 0
    }
    servers = get_servers_needing_history()
    if not servers:
        LOG.info("No servers need history backfill this cycle.")
        return stats

    LOG.info("Processing %d servers for definition history backfill.", len(servers))

    for i, server in enumerate(servers):
        server_id = server.get("server_id", "")
        current_version = int(server.get("current_version", 0))
        tool_schema = server.get("tool_schema")

        if not tool_schema or tool_schema == "" or tool_schema == "null":
            stats["skipped"] += 1
            continue

        try:
            new_version = backfill_server_history(server_id, current_version)
            if new_version > current_version:
                stats["new_versions"] += 1
                log_backfill_batch(server_id, "success", 1, new_version)
            else:
                log_backfill_batch(server_id, "no_change", 0, current_version)
            stats["processed"] += 1
        except Exception as e:
            LOG.error("Error backfilling history for %s: %s", server_id, e)
            stats["errors"] += 1
            log_backfill_batch(server_id, "error", 0, current_version, str(e))

        if (i + 1) % BATCH_SIZE == 0:
            LOG.info("Backfill batch complete: %d/%d servers processed.", i + 1, len(servers))
            send_heartbeat("running", {"processed": i + 1, "new_versions": stats["new_versions"]})

    LOG.info("Backfill cycle complete. processed=%d new_versions=%d errors=%d skipped=%d",
             stats["processed"], stats["new_versions"], stats["errors"], stats["skipped"])
    return stats


def run() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE)]
    )

    LOG.info("Starting %s on port %d", SERVICE_NAME, SERVICE_PORT)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        sys.exit(1)

    try:
        if not ensure_tables():
            LOG.error("Failed to ensure required tables, exiting.")
            sys.exit(1)

        send_heartbeat("starting")
        LOG.info("MCP definition history backfill service initialized.")

        while True:
            start = time.time()
            stats = cycle()
            elapsed = time.time() - start
            send_heartbeat("running", {"processed": stats["processed"], "elapsed_sec": round(elapsed, 2)})
            LOG.info("Sleeping %d seconds until next cycle.", POLL_SECS)
            time.sleep(POLL_SECS)

    except KeyboardInterrupt:
        LOG.info("Keyboard interrupt received.")
    finally:
        remove_pid_file()
        send_heartbeat("stopped")
        LOG.info("Service stopped.")


if __name__ == "__main__":
    run()