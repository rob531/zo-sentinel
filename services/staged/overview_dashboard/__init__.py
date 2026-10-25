import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel

SERVICE_NAME = "overview_dashboard"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

app = FastAPI(title=SERVICE_NAME)
router = APIRouter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.RequestException as exc:
        log.error("ws_query failed for SQL: %s -- %s", sql[:200], exc)
        raise HTTPException(status_code=502, detail=f"Query service unavailable: {exc}")


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("ws_write failed for table %s -- %s", table, exc)
        raise HTTPException(status_code=502, detail=f"Write service unavailable: {exc}")


class RegistrySummaryResponse(BaseModel):
    total_servers: int
    verdict_breakdown: Dict[str, int]
    trust_score_avg: Optional[float]
    unscored_count: int


class ThreatSummaryResponse(BaseModel):
    total_threat_associations: int
    threat_type_breakdown: Dict[str, int]
    high_severity_count: int


class RiskSummaryResponse(BaseModel):
    total_risk_entries: int
    risk_tier_breakdown: Dict[str, int]


class ServiceHealthSummary(BaseModel):
    total_services: int
    healthy_count: int
    stale_count: int
    services: List[Dict[str, Any]]


class AuditEventSummary(BaseModel):
    total_events: int
    recent_events: List[Dict[str, Any]]


class OverviewDashboardResponse(BaseModel):
    generated_at: str
    registry: RegistrySummaryResponse
    threats: ThreatSummaryResponse
    risk: RiskSummaryResponse
    services: ServiceHealthSummary
    audit: AuditEventSummary


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


@router.get("/overview", response_model=OverviewDashboardResponse)
def get_overview_dashboard() -> OverviewDashboardResponse:
    return OverviewDashboardResponse(
        generated_at=utc_now_iso(),
        registry=_get_registry_summary(),
        threats=_get_threat_summary(),
        risk=_get_risk_summary(),
        services=_get_service_health_summary(),
        audit=_get_audit_summary(),
    )


def _get_registry_summary() -> RegistrySummaryResponse:
    total = ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
    total_servers = total[0]["cnt"] if total else 0

    verdict_rows = ws_query(
        "SELECT verdict, COUNT(*) AS cnt "
        "FROM mcp_server_registry "
        "WHERE verdict IS NOT NULL "
        "GROUP BY verdict"
    )
    verdict_breakdown = {r["verdict"]: r["cnt"] for r in verdict_rows}

    avg_rows = ws_query(
        "SELECT AVG(trust_score) AS avg_score FROM mcp_server_registry WHERE trust_score IS NOT NULL"
    )
    trust_score_avg = avg_rows[0]["avg_score"] if avg_rows and avg_rows[0].get("avg_score") is not None else None

    scored_rows = ws_query("SELECT COUNT(DISTINCT server_id) AS cnt FROM mcp_signal_scores")
    scored_count = scored_rows[0]["cnt"] if scored_rows else 0
    unscored_count = max(0, total_servers - scored_count)

    return RegistrySummaryResponse(
        total_servers=total_servers,
        verdict_breakdown=verdict_breakdown,
        trust_score_avg=trust_score_avg,
        unscored_count=unscored_count,
    )


def _get_threat_summary() -> ThreatSummaryResponse:
    total_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_threat_associations")
    total_threat_associations = total_rows[0]["cnt"] if total_rows else 0

    type_rows = ws_query(
        "SELECT threat_type, COUNT(*) AS cnt "
        "FROM mcp_threat_associations "
        "WHERE threat_type IS NOT NULL "
        "GROUP BY threat_type"
    )
    threat_type_breakdown = {r["threat_type"]: r["cnt"] for r in type_rows}

    sev_rows = ws_query(
        "SELECT COUNT(*) AS cnt FROM mcp_threat_associations WHERE severity = 'HIGH' OR severity = 'CRITICAL'"
    )
    high_severity_count = sev_rows[0]["cnt"] if sev_rows else 0

    return ThreatSummaryResponse(
        total_threat_associations=total_threat_associations,
        threat_type_breakdown=threat_type_breakdown,
        high_severity_count=high_severity_count,
    )


def _get_risk_summary() -> RiskSummaryResponse:
    total_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_risk_register")
    total_risk_entries = total_rows[0]["cnt"] if total_rows else 0

    tier_rows = ws_query(
        "SELECT risk_tier, COUNT(*) AS cnt "
        "FROM mcp_risk_register "
        "WHERE risk_tier IS NOT NULL "
        "GROUP BY risk_tier"
    )
    risk_tier_breakdown = {r["risk_tier"]: r["cnt"] for r in tier_rows}

    return RiskSummaryResponse(
        total_risk_entries=total_risk_entries,
        risk_tier_breakdown=risk_tier_breakdown,
    )


def _get_service_health_summary() -> ServiceHealthSummary:
    health_rows = ws_query(
        "SELECT service, last_heartbeat FROM service_health ORDER BY service"
    )

    now = datetime.now(timezone.utc)
    healthy_count = 0
    stale_count = 0
    services = []

    for row in health_rows:
        hb_str = row.get("last_heartbeat", "")
        age_seconds = 0
        if hb_str:
            try:
                hb_dt = datetime.fromisoformat(hb_str.replace("Z", "+00:00"))
                age_seconds = (now - hb_dt).total_seconds()
            except (ValueError, TypeError):
                pass

        is_healthy = age_seconds < 300
        if is_healthy:
            healthy_count += 1
        else:
            stale_count += 1

        services.append({
            "service": row.get("service"),
            "last_heartbeat": hb_str,
            "age_seconds": int(age_seconds),
            "status": "HEALTHY" if is_healthy else "STALE",
        })

    return ServiceHealthSummary(
        total_services=len(services),
        healthy_count=healthy_count,
        stale_count=stale_count,
        services=services,
    )


def _get_audit_summary() -> AuditEventSummary:
    count_rows = ws_query("SELECT COUNT(*) AS cnt FROM audit_log")
    total_events = count_rows[0]["cnt"] if count_rows else 0

    recent_rows = ws_query(
        "SELECT event_id, event_type, actor, action, outcome, timestamp "
        "FROM audit_log "
        "ORDER BY timestamp DESC "
        "LIMIT 20"
    )
    recent_events = [
        {
            "event_id": r.get("event_id"),
            "event_type": r.get("event_type"),
            "actor": r.get("actor"),
            "action": r.get("action"),
            "outcome": r.get("outcome"),
            "timestamp": r.get("timestamp"),
        }
        for r in recent_rows
    ]

    return AuditEventSummary(
        total_events=total_events,
        recent_events=recent_events,
    )


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)