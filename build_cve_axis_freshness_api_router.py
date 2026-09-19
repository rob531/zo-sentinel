import os
import sys
import time
import logging
import signal
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from typing import Optional
import uvicorn

# ── Constants ─────────────────────────────────────────────────────────────────
SERVICE_NAME = "cve_axis_freshness_api"
PORT = 8791
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title=SERVICE_NAME, version="1.0.0")

# ── Helpers ───────────────────────────────────────────────────────────────────
def ws_query(sql: str) -> list:
    resp = requests.post(
        QUERY_SERVICE_URL,
        json={"sql": sql},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> None:
    resp = requests.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=30,
    )
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── PID / Signal ───────────────────────────────────────────────────────────────
def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance is running with PID %d", old_pid)
            sys.exit(1)
        except OSError:
            logger.warning("Stale PID file from %d, removing", old_pid)
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down", signum)
    remove_pid_file()
    sys.exit(0)


# ── Heartbeat ─────────────────────────────────────────────────────────────────
def send_heartbeat(status: str = "running", meta: Optional[dict] = None):
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": status,
            "ts": utc_now_iso(),
            "meta": meta or {},
        }])
    except Exception as e:
        logger.warning("Heartbeat failed: %s", e)


def heartbeat_loop():
    send_heartbeat("running")


# ── CVE Freshness Logic ───────────────────────────────────────────────────────
def get_cve_freshness_for_server(server_id: str) -> dict:
    """Return freshness metadata for a given server_id from threat_intel cache."""
    rows = ws_query(f"""
        SELECT
            computed_at,
            last_scanned,
            signal_name,
            score,
            evidence
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        ORDER BY computed_at DESC
        LIMIT 1
    """)
    if not rows:
        return {"server_id": server_id, "cve_fresh": False, "last_cve_at": None, "age_days": None}
    row = rows[0]
    computed_at_str = row.get("computed_at") or row.get("last_scanned")
    if computed_at_str:
        try:
            dt = datetime.fromisoformat(computed_at_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
            age_days = round(age_seconds / 86400, 2)
            fresh = age_days <= 30
            return {
                "server_id": server_id,
                "cve_fresh": fresh,
                "last_cve_at": computed_at_str,
                "age_days": age_days,
            }
        except Exception:
            pass
    return {"server_id": server_id, "cve_fresh": False, "last_cve_at": None, "age_days": None}


def get_global_cve_freshness_summary() -> dict:
    """Return global summary of CVE freshness across all servers with threat data."""
    rows = ws_query("""
        SELECT
            COUNT(*) AS total_servers_with_cve,
            SUM(CASE WHEN computed_at >= (NOW() - INTERVAL '30 days') THEN 1 ELSE 0 END) AS fresh_count,
            SUM(CASE WHEN computed_at < (NOW() - INTERVAL '30 days') THEN 1 ELSE 0 END) AS stale_count,
            MIN(computed_at) AS oldest_cve_at,
            MAX(computed_at) AS newest_cve_at
        FROM mcp_threat_associations
        WHERE computed_at IS NOT NULL
    """)
    if not rows:
        return {
            "total_servers_with_cve": 0,
            "fresh_count": 0,
            "stale_count": 0,
            "oldest_cve_at": None,
            "newest_cve_at": None,
            "freshness_pct": None,
        }
    r = rows[0]
    total = r.get("total_servers_with_cve", 0) or 0
    fresh = r.get("fresh_count", 0) or 0
    pct = round((fresh / total) * 100, 2) if total > 0 else None
    return {
        "total_servers_with_cve": total,
        "fresh_count": fresh,
        "stale_count": r.get("stale_count", 0) or 0,
        "oldest_cve_at": r.get("oldest_cve_at"),
        "newest_cve_at": r.get("newest_cve_at"),
        "freshness_pct": pct,
    }


def get_stale_cve_servers(age_threshold_days: int = 30, limit: int = 100) -> list:
    threshold_days_str = str(age_threshold_days)
    rows = ws_query(f"""
        SELECT
            server_id,
            MAX(computed_at) AS last_cve_at,
            MAX(last_scanned) AS last_scanned_at,
            COUNT(*) AS cve_count
        FROM mcp_threat_associations
        GROUP BY server_id
        HAVING MAX(computed_at) < (NOW() - INTERVAL '{threshold_days_str} days')
           OR (MAX(computed_at) IS NULL AND MAX(last_scanned) < (NOW() - INTERVAL '{threshold_days_str} days'))
        LIMIT {limit}
    """)
    result = []
    now = datetime.now(timezone.utc)
    for row in rows:
        computed_str = row.get("last_cve_at") or row.get("last_scanned_at")
        age_days = None
        if computed_str:
            try:
                dt = datetime.fromisoformat(computed_str.replace("Z", "+00:00"))
                age_days = round((now - dt).total_seconds() / 86400, 2)
            except Exception:
                pass
        result.append({
            "server_id": row.get("server_id"),
            "last_cve_at": computed_str,
            "age_days": age_days,
            "cve_count": row.get("cve_count", 0),
        })
    return result


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/api/cve-freshness/summary")
def cve_freshness_summary():
    """Return global CVE freshness summary across all servers."""
    return get_global_cve_freshness_summary()


@app.get("/api/cve-freshness/server/{server_id}")
def cve_freshness_for_server(server_id: str):
    """Return CVE freshness data for a specific server."""
    return get_cve_freshness_for_server(server_id)


@app.get("/api/cve-freshness/stale")
def cve_freshness_stale(
    threshold_days: int = Query(30, ge=1, le=365, description="Age threshold in days"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
):
    """Return servers with stale CVE data beyond threshold."""
    return get_stale_cve_servers(age_threshold_days=threshold_days, limit=limit)


@app.get("/api/cve-freshness/servers/{server_id}/history")
def cve_history_for_server(server_id: str, limit: int = Query(50, ge=1, le=500)):
    """Return CVE history for a specific server."""
    rows = ws_query(f"""
        SELECT
            threat_type,
            severity,
            evidence,
            reported_at,
            computed_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        ORDER BY computed_at DESC
        LIMIT {limit}
    """)
    return {"server_id": server_id, "history": rows, "count": len(rows)}


@app.get("/api/cve-freshness/dashboard")
def cve_dashboard():
    """Return combined dashboard of CVE freshness metrics."""
    summary = get_global_cve_freshness_summary()
    stale = get_stale_cve_servers(age_threshold_days=30, limit=50)
    return {
        "summary": summary,
        "stale_servers_sample": stale,
        "ts": utc_now_iso(),
    }


# ── Lifecycle ─────────────────────────────────────────────────────────────────
def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    send_heartbeat("starting")
    logger.info("Starting %s on port %d", SERVICE_NAME, PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()