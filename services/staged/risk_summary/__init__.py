"""Auto-emitted service package: risk_summary.

Provides aggregated risk views and signal summaries for the multi-tenant app tier.
Reads from the app Postgres/SQLite via SQLAlchemy ORM (NOT from DuckDB write_service).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
EXECUTE_URL = "http://localhost:8772/execute"
QUERY_URL = "http://localhost:8772/query"
SERVICE_NAME = "risk_summary"
SERVICE_PORT = 0
PID_FILE = "/tmp/risk_summary.pid"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write rows via write_service HTTP endpoint."""
    import requests
    url = f"{WRITE_SERVICE_URL}/write"
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service read endpoint."""
    import requests
    url = f"{QUERY_URL}"
    payload = {"sql": sql}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "ts": utc_now_iso(),
    }


def get_signal_scores(
    org_id: str,
    signal_names: Optional[List[str]] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get signal scores for an organization.
    
    Reads from mcp_server_registry and any signal-related data via write_service.
    """
    results = []
    
    if session is None:
        with get_session() as session:
            return get_signal_scores(org_id, signal_names, session)
    
    try:
        query = session.query(McpServerRegistry).filter(
            McpServerRegistry.org_id == org_id
        )
        
        for row in query.all():
            server_data = {
                "server_id": row.server_id,
                "name": row.name,
                "org_id": row.org_id,
            }
            results.append(server_data)
    except Exception as e:
        log.error("Error querying signal scores for org %s: %s", org_id, e)
    
    if signal_names:
        try:
            score_sql = """
                SELECT server_id, signal_name, score, metadata
                FROM mcp_signal_scores
                WHERE org_id = ?
                AND signal_name IN ({})
            """.format(",".join("?" for _ in signal_names))
            
            params = [org_id] + signal_names
            scores = ws_query(score_sql)
            
            for score_row in scores:
                results.append({
                    "server_id": score_row.get("server_id", ""),
                    "signal_name": score_row.get("signal_name", ""),
                    "score": score_row.get("score", 0.0),
                    "metadata": score_row.get("metadata", {}),
                })
        except Exception as e:
            log.warning("Could not fetch signal scores from write_service: %s", e)
    
    return results


def get_risk_register_summary(org_id: str) -> Dict[str, Any]:
    """Get risk register summary for an organization."""
    try:
        sql = f"""
            SELECT risk_tier, COUNT(*) as count
            FROM mcp_risk_register
            WHERE org_id = '{org_id}'
            GROUP BY risk_tier
        """
        rows = ws_query(sql)
        
        summary = {"total": 0, "by_tier": {}}
        for row in rows:
            tier = row.get("risk_tier", "UNKNOWN")
            count = row.get("count", 0)
            summary["by_tier"][tier] = count
            summary["total"] += count
        
        return summary
    except Exception as e:
        log.warning("Could not fetch risk register summary: %s", e)
        return {"total": 0, "by_tier": {}}


def get_signal_score_summary(org_id: str) -> Dict[str, Any]:
    """Get aggregated signal score summary."""
    try:
        sql = f"""
            SELECT signal_name, AVG(score) as avg_score, COUNT(*) as count
            FROM mcp_signal_scores
            WHERE org_id = '{org_id}'
            GROUP BY signal_name
        """
        rows = ws_query(sql)
        
        summary = []
        for row in rows:
            summary.append({
                "signal_name": row.get("signal_name", ""),
                "avg_score": row.get("avg_score", 0.0),
                "count": row.get("count", 0),
            })
        
        return {"signals": summary}
    except Exception as e:
        log.warning("Could not fetch signal score summary: %s", e)
        return {"signals": []}


def get_threat_summary(org_id: str) -> Dict[str, Any]:
    """Get threat association summary."""
    try:
        sql = f"""
            SELECT threat_type, severity, COUNT(*) as count
            FROM mcp_threat_associations
            WHERE org_id = '{org_id}'
            GROUP BY threat_type, severity
        """
        rows = ws_query(sql)
        
        summary = {"total": 0, "by_severity": {}}
        for row in rows:
            severity = row.get("severity", "UNKNOWN")
            count = row.get("count", 0)
            if severity not in summary["by_severity"]:
                summary["by_severity"][severity] = 0
            summary["by_severity"][severity] += count
            summary["total"] += count
        
        return summary
    except Exception as e:
        log.warning("Could not fetch threat summary: %s", e)
        return {"total": 0, "by_severity": {}}


def get_overview_dashboard(org_id: str) -> Dict[str, Any]:
    """Get complete risk overview dashboard for an organization."""
    registry_summary = {"total_servers": 0}
    try:
        with get_session() as session:
            count = session.query(McpServerRegistry).filter(
                McpServerRegistry.org_id == org_id
            ).count()
            registry_summary["total_servers"] = count
    except Exception as e:
        log.warning("Could not count registry entries: %s", e)
    
    risk_summary = get_risk_register_summary(org_id)
    signal_summary = get_signal_score_summary(org_id)
    threat_summary = get_threat_summary(org_id)
    
    return {
        "org_id": org_id,
        "registry": registry_summary,
        "risk": risk_summary,
        "signals": signal_summary,
        "threats": threat_summary,
        "generated_at": utc_now_iso(),
    }


def run():
    """Entry point for daemon mode."""
    import uvicorn
    from fastapi import FastAPI
    
    app = FastAPI()
    
    @app.get("/health")
    def health_route():
        return health()
    
    @app.get("/api/risk/{org_id}")
    def risk_overview(org_id: str):
        return get_overview_dashboard(org_id)
    
    @app.get("/api/signals/{org_id}")
    def signals(org_id: str, names: Optional[str] = None):
        signal_list = names.split(",") if names else None
        return get_signal_scores(org_id, signal_list)
    
    port = int(os.environ.get("PORT", SERVICE_PORT))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()