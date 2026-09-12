import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Depends, Header
from pydantic import BaseModel, Field

LOG_DIR = "/home/workspace/logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "verdict_watchlist_api.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SERVICE_NAME = "verdict_watchlist_api"
PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

app = FastAPI(title="Verdict Watchlist API", version="1.0.0")


class WatchlistEntry(BaseModel):
    server_id: str = Field(..., description="MCP server ID to watch")
    reason: Optional[str] = Field(None, description="Reason for watching")
    priority: str = Field(default="normal", description="Priority: low, normal, high, critical")
    tags: List[str] = Field(default_factory=list, description="Custom tags")
    added_by: str = Field(..., description="Analyst who added the entry")


class WatchlistUpdate(BaseModel):
    reason: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None


class VerdictChangeAlert(BaseModel):
    server_id: str
    server_name: str
    previous_verdict: Optional[str]
    new_verdict: str
    previous_trust_score: Optional[float]
    new_trust_score: float
    risk_tier: str
    timestamp: str


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    url = get_write_url()
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    url = get_query_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_execute(sql: str) -> Dict[str, Any]:
    url = get_execute_url()
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS verdict_watchlist (
        entry_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        server_name VARCHAR,
        reason VARCHAR,
        priority VARCHAR DEFAULT 'normal',
        tags JSON,
        added_by VARCHAR,
        added_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        active BOOLEAN DEFAULT true,
        UNIQUE(server_id)
    )
    """
    ws_execute(sql)

    sql = """
    CREATE TABLE IF NOT EXISTS verdict_watchlist_history (
        history_id VARCHAR PRIMARY KEY,
        entry_id VARCHAR,
        event_type VARCHAR,
        previous_verdict VARCHAR,
        new_verdict VARCHAR,
        previous_trust_score DOUBLE,
        new_trust_score DOUBLE,
        event_at TIMESTAMPTZ,
        details JSON
    )
    """
    ws_execute(sql)

    sql = """
    CREATE TABLE IF NOT EXISTS verdict_watchlist_alerts (
        alert_id VARCHAR PRIMARY KEY,
        entry_id VARCHAR,
        server_id VARCHAR,
        event_type VARCHAR,
        previous_verdict VARCHAR,
        new_verdict VARCHAR,
        previous_trust_score DOUBLE,
        new_trust_score DOUBLE,
        risk_tier VARCHAR,
        alert_at TIMESTAMPTZ,
        acknowledged BOOLEAN DEFAULT false,
        acknowledged_by VARCHAR,
        acknowledged_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)


def generate_entry_id() -> str:
    return f"wl_{uuid.uuid4().hex[:12]}"


def generate_history_id() -> str:
    return f"wlh_{uuid.uuid4().hex[:12]}"


def generate_alert_id() -> str:
    return f"wla_{uuid.uuid4().hex[:12]}"


@app.on_event("startup")
async def startup():
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    ensure_tables()
    log.info(f"{SERVICE_NAME} startup complete")


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "version": "1.0.0", "timestamp": utc_now_iso()}


@app.post("/watchlist/add")
async def add_to_watchlist(entry: WatchlistEntry):
    entry_id = generate_entry_id()
    now = utc_now_iso()

    server_name = None
    rows = ws_query(f"SELECT name FROM mcp_server_registry WHERE server_id = '{entry.server_id}'")
    if rows:
        server_name = rows[0].get("name")

    row = {
        "entry_id": entry_id,
        "server_id": entry.server_id,
        "server_name": server_name,
        "reason": entry.reason,
        "priority": entry.priority,
        "tags": entry.tags,
        "added_by": entry.added_by,
        "added_at": now,
        "updated_at": now,
        "active": True,
    }

    ws_write("verdict_watchlist", [row])

    history_id = generate_history_id()
    history_row = {
        "history_id": history_id,
        "entry_id": entry_id,
        "event_type": "added",
        "previous_verdict": None,
        "new_verdict": None,
        "previous_trust_score": None,
        "new_trust_score": None,
        "event_at": now,
        "details": {"added_by": entry.added_by, "reason": entry.reason},
    }
    ws_write("verdict_watchlist_history", [history_row])

    log.info(f"Added server {entry.server_id} to watchlist by {entry.added_by}")

    return {"status": "added", "entry_id": entry_id, "server_id": entry.server_id}


@app.delete("/watchlist/remove/{server_id}")
async def remove_from_watchlist(server_id: str, removed_by: str = Query(...)):
    now = utc_now_iso()

    existing = ws_query(f"SELECT entry_id, active FROM verdict_watchlist WHERE server_id = '{server_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not in watchlist")

    entry_id = existing[0]["entry_id"]

    sql = f"UPDATE verdict_watchlist SET active = false, updated_at = '{now}' WHERE server_id = '{server_id}'"
    ws_execute(sql)

    history_id = generate_history_id()
    history_row = {
        "history_id": history_id,
        "entry_id": entry_id,
        "event_type": "removed",
        "previous_verdict": None,
        "new_verdict": None,
        "previous_trust_score": None,
        "new_trust_score": None,
        "event_at": now,
        "details": {"removed_by": removed_by},
    }
    ws_write("verdict_watchlist_history", [history_row])

    log.info(f"Removed server {server_id} from watchlist by {removed_by}")

    return {"status": "removed", "server_id": server_id}


@app.get("/watchlist")
async def get_watchlist(
    active_only: bool = Query(True),
    priority: Optional[str] = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
):
    conditions = []
    if active_only:
        conditions.append("active = true")
    if priority:
        conditions.append(f"priority = '{priority}'")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"""
        SELECT entry_id, server_id, server_name, reason, priority, tags, added_by, added_at, updated_at, active
        FROM verdict_watchlist
        {where_clause}
        ORDER BY 
            CASE priority 
                WHEN 'critical' THEN 1 
                WHEN 'high' THEN 2 
                WHEN 'normal' THEN 3 
                WHEN 'low' THEN 4 
            END,
            added_at DESC
        LIMIT {limit} OFFSET {offset}
    """
    rows = ws_query(sql)

    for row in rows:
        if row.get("tags") and isinstance(row["tags"], str):
            try:
                import json
                row["tags"] = json.loads(row["tags"])
            except Exception:
                row["tags"] = []

    count_sql = f"SELECT COUNT(*) as total FROM verdict_watchlist {where_clause}"
    count_rows = ws_query(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/watchlist/server/{server_id}")
async def get_server_watchlist_entry(server_id: str):
    sql = f"""
        SELECT entry_id, server_id, server_name, reason, priority, tags, added_by, added_at, updated_at, active
        FROM verdict_watchlist
        WHERE server_id = '{server_id}'
    """
    rows = ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not in watchlist")

    row = rows[0]
    if row.get("tags") and isinstance(row["tags"], str):
        try:
            import json
            row["tags"] = json.loads(row["tags"])
        except Exception:
            row["tags"] = []

    return row


@app.patch("/watchlist/update/{server_id}")
async def update_watchlist_entry(server_id: str, update: WatchlistUpdate, updated_by: str = Query(...)):
    now = utc_now_iso()

    existing = ws_query(f"SELECT entry_id FROM verdict_watchlist WHERE server_id = '{server_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not in watchlist")

    entry_id = existing[0]["entry_id"]
    updates = []
    if update.reason is not None:
        updates.append(f"reason = '{update.reason}'")
    if update.priority is not None:
        updates.append(f"priority = '{update.priority}'")
    if update.tags is not None:
        import json
        tags_json = json.dumps(update.tags).replace("'", "''")
        updates.append(f"tags = '{tags_json}'")

    updates.append(f"updated_at = '{now}'")

    sql = f"UPDATE verdict_watchlist SET {', '.join(updates)} WHERE server_id = '{server_id}'"
    ws_execute(sql)

    log.info(f"Updated watchlist entry for {server_id} by {updated_by}")

    return {"status": "updated", "server_id": server_id}


@app.get("/watchlist/alerts")
async def get_alerts(
    acknowledged: Optional[bool] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    conditions = []
    if acknowledged is not None:
        conditions.append(f"acknowledged = {acknowledged}")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"""
        SELECT alert_id, entry_id, server_id, event_type, previous_verdict, new_verdict,
               previous_trust_score, new_trust_score, risk_tier, alert_at, acknowledged, acknowledged_by, acknowledged_at
        FROM verdict_watchlist_alerts
        {where_clause}
        ORDER BY alert_at DESC
        LIMIT {limit} OFFSET {offset}
    """
    rows = ws_query(sql)

    count_sql = f"SELECT COUNT(*) as total FROM verdict_watchlist_alerts {where_clause}"
    count_rows = ws_query(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app.post("/watchlist/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, acknowledged_by: str = Query(...)):
    now = utc_now_iso()

    sql = f"""
        UPDATE verdict_watchlist_alerts 
        SET acknowledged = true, acknowledged_by = '{acknowledged_by}', acknowledged_at = '{now}'
        WHERE alert_id = '{alert_id}'
    """
    ws_execute(sql)

    log.info(f"Alert {alert_id} acknowledged by {acknowledged_by}")

    return {"status": "acknowledged", "alert_id": alert_id}


@app.get("/watchlist/history/{server_id}")
async def get_server_history(server_id: str, limit: int = Query(50)):
    sql = f"""
        SELECT history_id, entry_id, event_type, previous_verdict, new_verdict,
               previous_trust_score, new_trust_score, event_at, details
        FROM verdict_watchlist_history
        WHERE entry_id IN (SELECT entry_id FROM verdict_watchlist WHERE server_id = '{server_id}')
        ORDER BY event_at DESC
        LIMIT {limit}
    """
    rows = ws_query(sql)
    return {"server_id": server_id, "history": rows}


@app.get("/watchlist/verdict-changes")
async def get_verdict_changes_since(since: str = Query(..., description="ISO timestamp")):
    watchlist_servers = ws_query("SELECT server_id FROM verdict_watchlist WHERE active = true")
    server_ids = [r["server_id"] for r in watchlist_servers]

    if not server_ids:
        return {"changes": [], "count": 0}

    server_id_list = "', '".join(server_ids)
    sql = f"""
        SELECT 
            r.server_id,
            r.name as server_name,
            r.verdict as current_verdict,
            r.trust_score as current_trust_score,
            COALESCE(h.verdict, r.verdict) as previous_verdict,
            COALESCE(h.trust_score, r.trust_score) as previous_trust_score,
            r.risk_tier,
            r.last_assessed
        FROM mcp_server_registry r
        LEFT JOIN (
            SELECT server_id, verdict, trust_score, computed_at
            FROM mcp_signal_scores
            WHERE computed_at <= '{since}'
        ) h ON r.server_id = h.server_id
        WHERE r.server_id IN ('{server_id_list}')
        AND (
            r.verdict != COALESCE(h.verdict, r.verdict)
            OR ABS(r.trust_score - COALESCE(h.trust_score, r.trust_score)) > 0.1
        )
    """
    rows = ws_query(sql)
    return {"changes": rows, "count": len(rows)}


@app.post("/watchlist/scan")
async def scan_watchlist_for_changes():
    now = utc_now_iso()
    watchlist_servers = ws_query("SELECT entry_id, server_id, server_name FROM verdict_watchlist WHERE active = true")

    changes_found = []

    for entry in watchlist_servers:
        server_id = entry["server_id"]
        entry_id = entry["entry_id"]
        server_name = entry.get("server_name") or server_id

        sql = f"""
            SELECT verdict, trust_score, risk_tier, last_assessed
            FROM mcp_server_registry
            WHERE server_id = '{server_id}'
        """
        registry_rows = ws_query(sql)

        if not registry_rows:
            continue

        current = registry_rows[0]
        current_verdict = current.get("verdict")
        current_score = current.get("trust_score") or 0.0
        risk_tier = current.get("risk_tier") or "unknown"

        last_sql = f"""
            SELECT previous_verdict, new_verdict, event_at
            FROM verdict_watchlist_history
            WHERE entry_id = '{entry_id}' AND event_type = 'verdict_change'
            ORDER BY event_at DESC
            LIMIT 1
        """
        last_rows = ws_query(last_sql)

        previous_verdict = None
        previous_score = None

        if last_rows:
            previous_verdict = last_rows[0].get("new_verdict")
            prev_reg_sql = f"""
                SELECT trust_score FROM mcp_server_registry
                WHERE server_id = '{server_id}' AND last_assessed < '{last_rows[0].get("event_at")}'
                ORDER BY last_assessed DESC LIMIT 1
            """
            prev_rows = ws_query(prev_reg_sql)
            if prev_rows:
                previous_score = prev_rows[0].get("trust_score") or 0.0

        if previous_verdict and previous_verdict != current_verdict:
            alert_id = generate_alert_id()
            alert_row = {
                "alert_id": alert_id,
                "entry_id": entry_id,
                "server_id": server_id,
                "event_type": "verdict_change",
                "previous_verdict": previous_verdict,
                "new_verdict": current_verdict,
                "previous_trust_score": previous_score,
                "new_trust_score": current_score,
                "risk_tier": risk_tier,
                "alert_at": now,
                "acknowledged": False,
                "acknowledged_by": None,
                "acknowledged_at": None,
            }
            ws_write("verdict_watchlist_alerts", [alert_row])

            history_id = generate_history_id()
            history_row = {
                "history_id": history_id,
                "entry_id": entry_id,
                "event_type": "verdict_change",
                "previous_verdict": previous_verdict,
                "new_verdict": current_verdict,
                "previous_trust_score": previous_score,
                "new_trust_score": current_score,
                "event_at": now,
                "details": {"risk_tier": risk_tier},
            }
            ws_write("verdict_watchlist_history", [history_row])

            changes_found.append(
                {
                    "server_id": server_id,
                    "server_name": server_name,
                    "previous_verdict": previous_verdict,
                    "new_verdict": current_verdict,
                    "previous_score": previous_score,
                    "new_score": current_score,
                    "risk_tier": risk_tier,
                }
            )

    log.info(f"Watchlist scan complete: {len(changes_found)} verdict changes detected")

    return {"status": "scan_complete", "changes_detected": len(changes_found), "changes": changes_found}


@app.get("/watchlist/stats")
async def get_watchlist_stats():
    total_entries = ws_query("SELECT COUNT(*) as cnt FROM verdict_watchlist WHERE active = true")
    total = total_entries[0]["cnt"] if total_entries else 0

    priority_sql = """
        SELECT priority, COUNT(*) as cnt 
        FROM verdict_watchlist 
        WHERE active = true 
        GROUP BY priority
    """
    priority_rows = ws_query(priority_sql)
    by_priority = {r["priority"]: r["cnt"] for r in priority_rows}

    unacknowledged_sql = "SELECT COUNT(*) as cnt FROM verdict_watchlist_alerts WHERE acknowledged = false"
    unack_rows = ws_query(unacknowledged_sql)
    unacknowledged = unack_rows[0]["cnt"] if unack_rows else 0

    today_sql = f"SELECT COUNT(*) as cnt FROM verdict_watchlist_alerts WHERE alert_at >= CURRENT_DATE"
    today_rows = ws_query(today_sql)
    alerts_today = today_rows[0]["cnt"] if today_rows else 0

    return {
        "total_active": total,
        "by_priority": by_priority,
        "unacknowledged_alerts": unacknowledged,
        "alerts_today": alerts_today,
    }


@app.get("/watchlist/search")
async def search_watchlist(
    q: str = Query(..., description="Search query"),
    limit: int = Query(50),
):
    search_term = q.replace("'", "''")
    sql = f"""
        SELECT entry_id, server_id, server_name, reason, priority, tags, added_by, added_at
        FROM verdict_watchlist
        WHERE active = true
        AND (server_id ILIKE '%{search_term}%' OR server_name ILIKE '%{search_term}%' OR reason ILIKE '%{search_term}%')
        ORDER BY added_at DESC
        LIMIT {limit}
    """
    rows = ws_query(sql)

    for row in rows:
        if row.get("tags") and isinstance(row["tags"], str):
            try:
                import json
                row["tags"] = json.loads(row["tags"])
            except Exception:
                row["tags"] = []

    return {"items": rows, "count": len(rows)}


def run():
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    ensure_tables()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()