import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("/home/workspace/logs/overview_dashboard.log")],
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "overview_dashboard"
PORT = 8791
PID_FILE = "/tmp/overview_dashboard.pid"


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql)
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table=%s", e, table)
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- Pydantic response models ----


class TierDistribution(BaseModel):
    risk_tier: str
    count: int


class ServerSummary(BaseModel):
    total_servers: int
    scored_servers: int
    tier_distribution: List[TierDistribution]
    computed_at: str


class ThreatOverview(BaseModel):
    threat_count: int
    servers_with_threats: int


class TrendPoint(BaseModel):
    date: str
    total_servers: int
    scored_servers: int


class TrendResponse(BaseModel):
    days: int
    points: List[TrendPoint]


class OverviewDashboardResponse(BaseModel):
    summary: ServerSummary
    threats: ThreatOverview
    trend: TrendResponse


# ---- FastAPI app ----

app = FastAPI(title="Overview Dashboard API", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/api/overview/dashboard", response_model=OverviewDashboardResponse)
def get_overview_dashboard(days: int = 30):
    total_servers = _get_total_servers()
    scored_servers = _get_scored_servers()
    tier_dist = _get_tier_distribution()
    threats = _get_threat_overview()
    trend = _get_trend_series(days)

    return OverviewDashboardResponse(
        summary=ServerSummary(
            total_servers=total_servers,
            scored_servers=scored_servers,
            tier_distribution=tier_dist,
            computed_at=utc_now_iso(),
        ),
        threats=threats,
        trend=TrendResponse(days=days, points=trend),
    )


def _get_total_servers() -> int:
    rows = ws_query("SELECT COUNT(*) AS total FROM mcp_server_registry")
    if rows:
        return rows[0].get("total", 0) or 0
    return 0


def _get_scored_servers() -> int:
    rows = ws_query(
        "SELECT COUNT(DISTINCT server_id) AS scored FROM mcp_risk_register"
    )
    if rows:
        return rows[0].get("scored", 0) or 0
    return 0


def _get_tier_distribution() -> List[TierDistribution]:
    rows = ws_query(
        """
        SELECT risk_tier, COUNT(*) AS count
        FROM mcp_risk_register
        GROUP BY risk_tier
        ORDER BY risk_tier
        """
    )
    return [
        TierDistribution(risk_tier=r.get("risk_tier", "UNKNOWN"), count=r.get("count", 0) or 0)
        for r in rows
    ]


def _get_threat_overview() -> ThreatOverview:
    threat_count = 0
    servers_with_threats = 0

    ta_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_threat_associations")
    if ta_rows:
        threat_count = ta_rows[0].get("cnt", 0) or 0

    th_rows = ws_query(
        """
        SELECT COUNT(DISTINCT server_id) AS servers
        FROM mcp_threat_associations
        """
    )
    if th_rows:
        servers_with_threats = th_rows[0].get("servers", 0) or 0

    return ThreatOverview(
        threat_count=threat_count,
        servers_with_threats=servers_with_threats,
    )


def _get_trend_series(days: int = 30) -> List[TrendPoint]:
    # mcp_risk_register.computed_at is the most stable trend anchor.
    # We emit one point per day for the last N days by aggregating
    # computed_at timestamps, falling back to current counts with null
    # date for days with no activity.
    today = datetime.now(timezone.utc).date()
    rows = ws_query(
        f"""
        SELECT
            DATE(computed_at) AS dt,
            COUNT(DISTINCT server_id) AS scored_servers
        FROM mcp_risk_register
        WHERE computed_at >= TIMESTAMP '{today.isoformat()}' - INTERVAL '{days} days'
        GROUP BY DATE(computed_at)
        ORDER BY dt
        """
    )
    # Build a map of date -> scored_servers
    date_map: Dict[str, int] = {}
    for r in rows:
        dt = r.get("dt")
        if dt:
            date_map[str(dt)] = r.get("scored_servers", 0) or 0

    # Fill in all days; carry forward last known value as approximation
    points: List[TrendPoint] = []
    running_total = _get_total_servers()
    last_scored = 0
    for i in range(days - 1, -1, -1):
        d = today - __import__("datetime").timedelta(days=i)
        ds = d.isoformat()
        last_scored = date_map.get(ds, last_scored)
        points.append(
            TrendPoint(date=ds, total_servers=running_total, scored_servers=last_scored)
        )
    return points


# ---- Daemon lifecycle ----


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error("Another instance already running with PID %d", old_pid)
            sys.exit(1)
        except OSError:
            log.warning("Stale PID file; removing.")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame) -> None:
    log.info("Received signal %d; shutting down.", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> None:
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": "running",
        "meta": "{}",
    }])


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("Starting %s on port %d", SERVICE_NAME, PORT)
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()