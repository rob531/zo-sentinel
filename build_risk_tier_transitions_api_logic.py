import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests

SERVICE_NAME = "risk_tier_transitions_api_logic"
SERVICE_PORT = 8785
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8772")
QUERY_URL = os.environ.get("QUERY_SERVICE_URL", "http://localhost:8772/query")
EXECUTE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://localhost:8772/execute")
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

LOG_DIR_path = __import__("pathlib").Path(LOG_DIR)
LOG_DIR_path.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ws_query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"ws_write failed: {e}")
        return {"ok": False, "error": str(e)}


def ws_execute(sql: str, params: list | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"ws_execute failed: {e}")
        return {"ok": False, "error": str(e)}


def ensure_tables() -> None:
    ws_execute("""
        CREATE TABLE IF NOT EXISTS risk_tier_transitions (
            transition_id VARCHAR PRIMARY KEY,
            server_id VARCHAR NOT NULL,
            from_risk_tier VARCHAR,
            to_risk_tier VARCHAR NOT NULL,
            trigger_source VARCHAR,
            trigger_reason VARCHAR,
            transitioned_at TIMESTAMPTZ NOT NULL,
            metadata JSON
        )
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS risk_tier_change_log (
            id BIGINT AUTOINCREMENT,
            server_id VARCHAR NOT NULL,
            from_tier VARCHAR,
            to_tier VARCHAR NOT NULL,
            reason VARCHAR,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (id, server_id)
        )
    """)


def get_transition_by_id(transition_id: str) -> dict[str, Any] | None:
    rows = ws_query(
        "SELECT * FROM risk_tier_transitions WHERE transition_id = ?",
        [transition_id]
    )
    return rows[0] if rows else None


def get_transitions_by_server(
    server_id: str,
    limit: int = 100,
    offset: int = 0
) -> list[dict[str, Any]]:
    return ws_query(
        """
        SELECT * FROM risk_tier_transitions 
        WHERE server_id = ? 
        ORDER BY transitioned_at DESC 
        LIMIT ? OFFSET ?
        """,
        [server_id, limit, offset]
    )


def get_transitions_by_timerange(
    from_date: str,
    to_date: str,
    risk_tier: str | None = None,
    limit: int = 500
) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM risk_tier_transitions 
        WHERE transitioned_at >= ? AND transitioned_at <= ?
    """
    params: list[Any] = [from_date, to_date]
    if risk_tier:
        sql += " AND (from_risk_tier = ? OR to_risk_tier = ?)"
        params.extend([risk_tier, risk_tier])
    sql += " ORDER BY transitioned_at DESC LIMIT ?"
    params.append(limit)
    return ws_query(sql, params)


def get_transition_counts_by_tier() -> dict[str, int]:
    rows = ws_query("""
        SELECT to_risk_tier, COUNT(*) as count 
        FROM risk_tier_transitions 
        GROUP BY to_risk_tier
    """)
    return {row["to_risk_tier"]: row["count"] for row in rows}


def get_transition_velocity(
    from_date: str,
    to_date: str
) -> dict[str, int]:
    rows = ws_query(
        """
        SELECT DATE(transitioned_at) as transition_date, COUNT(*) as count
        FROM risk_tier_transitions
        WHERE transitioned_at >= ? AND transitioned_at <= ?
        GROUP BY DATE(transitioned_at)
        ORDER BY transition_date
        """,
        [from_date, to_date]
    )
    return {row["transition_date"]: row["count"] for row in rows}


def record_transition(
    server_id: str,
    from_tier: str | None,
    to_tier: str,
    trigger_source: str = "system",
    trigger_reason: str | None = None,
    metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    import hashlib
    import json
    
    content = f"{server_id}:{from_tier}:{to_tier}:{utc_now_iso()}"
    transition_id = hashlib.sha256(content.encode()).hexdigest()[:32]
    
    transition_record = {
        "transition_id": transition_id,
        "server_id": server_id,
        "from_risk_tier": from_tier,
        "to_risk_tier": to_tier,
        "trigger_source": trigger_source,
        "trigger_reason": trigger_reason,
        "transitioned_at": utc_now_iso(),
        "metadata": json.dumps(metadata) if metadata else None
    }
    
    result = ws_write("risk_tier_transitions", [transition_record])
    if result.get("ok"):
        return {"success": True, "transition_id": transition_id}
    return {"success": False, "error": result.get("error", "unknown")}


def detect_and_record_changes(
    server_id: str,
    current_tier: str,
    previous_tier: str | None,
    trigger_reason: str | None = None
) -> dict[str, Any]:
    if previous_tier is None or previous_tier == current_tier:
        return {"changed": False, "transition_id": None}
    
    result = record_transition(
        server_id=server_id,
        from_tier=previous_tier,
        to_tier=current_tier,
        trigger_source="risk_ranker",
        trigger_reason=trigger_reason,
        metadata={"detected_at": utc_now_iso()}
    )
    
    return {
        "changed": True,
        "transition_id": result.get("transition_id"),
        "from_tier": previous_tier,
        "to_tier": current_tier
    }


def get_latest_tier_for_server(server_id: str) -> str | None:
    rows = ws_query(
        """
        SELECT to_risk_tier FROM risk_tier_transitions 
        WHERE server_id = ? 
        ORDER BY transitioned_at DESC 
        LIMIT 1
        """,
        [server_id]
    )
    return rows[0]["to_risk_tier"] if rows else None


def get_transition_path(
    server_id: str
) -> list[dict[str, Any]]:
    return ws_query(
        """
        SELECT from_risk_tier, to_risk_tier, transitioned_at, trigger_reason
        FROM risk_tier_transitions 
        WHERE server_id = ? 
        ORDER BY transitioned_at ASC
        """,
        [server_id]
    )


def get_escalation_candidates(
    min_risk_tier: str = "MEDIUM",
    hours_threshold: int = 24
) -> list[dict[str, Any]]:
    return ws_query(
        """
        SELECT r.server_id, r.risk_tier, r.threat_count, t.transitioned_at
        FROM mcp_risk_register r
        JOIN risk_tier_transitions t ON r.server_id = t.server_id
        WHERE r.risk_tier >= ? 
        AND t.transitioned_at >= TIMESTAMPTZ ? - INTERVAL '1 hour' * ?
        AND NOT EXISTS (
            SELECT 1 FROM risk_tier_transitions t2 
            WHERE t2.server_id = r.server_id 
            AND t2.transitioned_at > t.transitioned_at
        )
        """,
        [min_risk_tier, utc_now_iso(), hours_threshold]
    )


def get_transition_summary_stats(
    from_date: str,
    to_date: str
) -> dict[str, Any]:
    total = ws_query(
        """
        SELECT COUNT(*) as total FROM risk_tier_transitions 
        WHERE transitioned_at >= ? AND transitioned_at <= ?
        """,
        [from_date, to_date]
    )
    
    by_direction = ws_query(
        """
        SELECT from_risk_tier, to_risk_tier, COUNT(*) as count
        FROM risk_tier_transitions
        WHERE transitioned_at >= ? AND transitioned_at <= ?
        GROUP BY from_risk_tier, to_risk_tier
        """,
        [from_date, to_date]
    )
    
    by_source = ws_query(
        """
        SELECT trigger_source, COUNT(*) as count
        FROM risk_tier_transitions
        WHERE transitioned_at >= ? AND transitioned_at <= ?
        GROUP BY trigger_source
        """,
        [from_date, to_date]
    )
    
    return {
        "total_transitions": total[0]["total"] if total else 0,
        "by_direction": by_direction,
        "by_source": by_source,
        "period": {"from": from_date, "to": to_date}
    }


def compute_transition_rate(
    server_ids: list[str] | None = None,
    days: int = 7
) -> float:
    from_dt = utc_now_iso()
    import sys
    sys.path.insert(0, '/home/workspace')
    from datetime import timedelta
    to_dt = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    
    if server_ids:
        placeholders = ",".join(["?"] * len(server_ids))
        sql = f"""
            SELECT COUNT(*) as count FROM risk_tier_transitions 
            WHERE transitioned_at >= ? AND transitioned_at <= ?
            AND server_id IN ({placeholders})
        """
        rows = ws_query(sql, [to_dt, from_dt] + server_ids)
    else:
        rows = ws_query(
            """
            SELECT COUNT(*) as count FROM risk_tier_transitions 
            WHERE transitioned_at >= ? AND transitioned_at <= ?
            """,
            [to_dt, from_dt]
        )
    
    total_transitions = rows[0]["count"] if rows else 0
    return total_transitions / max(days, 1)


def bulk_record_transitions(
    transitions: list[dict[str, Any]]
) -> dict[str, Any]:
    records = []
    import hashlib
    import json
    
    for t in transitions:
        content = f"{t['server_id']}:{t.get('from_tier')}:{t['to_tier']}:{utc_now_iso()}"
        transition_id = hashlib.sha256(content.encode()).hexdigest()[:32]
        records.append({
            "transition_id": transition_id,
            "server_id": t["server_id"],
            "from_risk_tier": t.get("from_tier"),
            "to_risk_tier": t["to_tier"],
            "trigger_source": t.get("trigger_source", "system"),
            "trigger_reason": t.get("reason"),
            "transitioned_at": utc_now_iso(),
            "metadata": json.dumps(t.get("metadata")) if t.get("metadata") else None
        })
    
    if not records:
        return {"success": True, "count": 0}
    
    result = ws_write("risk_tier_transitions", records)
    return {
        "success": result.get("ok", False),
        "count": len(records),
        "error": result.get("error")
    }


def get_stale_escalations(
    hours_threshold: int = 48
) -> list[dict[str, Any]]:
    return ws_query(
        """
        SELECT r.server_id, r.risk_tier, r.threat_count,
               t.transitioned_at,
               EXTRACT(EPOCH FROM (NOW() - t.transitioned_at::TIMESTAMPTZ)) / 3600 as hours_since_transition
        FROM mcp_risk_register r
        JOIN LATERAL (
            SELECT transitioned_at FROM risk_tier_transitions 
            WHERE server_id = r.server_id 
            ORDER BY transitioned_at DESC 
            LIMIT 1
        ) t ON true
        WHERE r.risk_tier IN ('HIGH', 'CRITICAL')
        AND EXTRACT(EPOCH FROM (NOW() - t.transitioned_at::TIMESTAMPTZ)) / 3600 > ?
        ORDER BY hours_since_transition DESC
        """,
        [hours_threshold]
    )


if __name__ == "__main__":
    ensure_tables()
    log.info(f"{SERVICE_NAME} initialized")
    sys.exit(0)