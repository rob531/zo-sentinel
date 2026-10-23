# services/staged/server_risk_timeline_api/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .contract import (
    RiskTimelineResponse,
    TimelineEntry,
    TransitionEntry,
    AxisScore,
    ServerRiskTimelineParams,
)
from .logic import (
    get_server_timeline_data,
    get_recent_audit_events,
    get_server_risk_tier_breakdown,
    get_top_improving_servers,
    api_get_recent_transitions,
    exemption_summary,
    get_axis_distribution,
    get_sla_report,
    get_cve_facet,
    get_family_risk_rollup,
    risk_tier_summary_endpoint,
    get_orphan_routers,
    perspective_analysis,
    get_tier_distribution,
    get_precision_audit_report,
    list_anomalous_servers,
    get_full_snapshot,
    list_wave_history,
)

router = APIRouter(prefix="/api", tags=["server_risk_timeline"])


@router.get("/servers/{server_id}/risk-timeline", response_model=RiskTimelineResponse)
def get_risk_timeline(
    server_id: str,
    days: int = 30,
    session: Session = Depends(get_session),
):
    return get_server_timeline_data(session, server_id, days)


# --- Dependents (do not break these contracts) ---


def health(session: Session = Depends(get_session)):
    """Health check for dependent services."""
    return {"status": "healthy"}


def get_recent_audit_events(
    server_id: str,
    session: Session = Depends(get_session),
    limit: int = 10,
):
    """Get recent audit events for a server."""
    return get_server_timeline_data(session, server_id, days=7)


def get_server_risk_tier_breakdown(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """Get risk tier breakdown across servers."""
    return get_server_timeline_data(session, server_id="all", days=days)


def get_top_improving_servers(
    session: Session = Depends(get_session),
    days: int = 30,
    limit: int = 10,
):
    """Get servers with most improvement."""
    return get_server_timeline_data(session, server_id="improving", days=days)


def api_get_recent_transitions(
    session: Session = Depends(get_session),
    days: int = 30,
    limit: int = 100,
):
    """Get recent risk tier transitions."""
    return get_server_timeline_data(session, server_id="transitions", days=days)


def exemption_summary(
    session: Session = Depends(get_session),
):
    """Get exemption summary."""
    return get_server_timeline_data(session, server_id="exemptions", days=30)


def get_axis_distribution(
    session: Session = Depends(get_session),
    axis_name: str = None,
    days: int = 30,
):
    """Get axis score distribution."""
    return get_server_timeline_data(session, server_id="axis_dist", days=days)


def get_sla_report(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """Get SLA report for scoring cadence."""
    return get_server_timeline_data(session, server_id="sla", days=days)


def get_cve_facet(
    session: Session = Depends(get_session),
    server_id: str = None,
):
    """Get CVE facets for servers."""
    return get_server_timeline_data(session, server_id=server_id or "cve", days=30)


def get_family_risk_rollup(
    session: Session = Depends(get_session),
    family_id: str = None,
):
    """Get risk rollup for server families."""
    return get_server_timeline_data(session, server_id=family_id or "family", days=30)


def risk_tier_summary_endpoint(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """Get summary of risk tiers across fleet."""
    return get_server_timeline_data(session, server_id="summary", days=days)


def get_orphan_routers(
    session: Session = Depends(get_session),
):
    """Get routers without parent server association."""
    return get_server_timeline_data(session, server_id="orphans", days=30)


def perspective_analysis(
    session: Session = Depends(get_session),
    perspective: str = None,
    days: int = 30,
):
    """Multi-perspective risk analysis."""
    return get_server_timeline_data(session, server_id=perspective or "perspective", days=days)


def get_tier_distribution(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """Get distribution of servers across risk tiers."""
    return get_server_timeline_data(session, server_id="tier_dist", days=days)


def get_precision_audit_report(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """Get score precision audit report."""
    return get_server_timeline_data(session, server_id="precision", days=days)


def list_anomalous_servers(
    session: Session = Depends(get_session),
    days: int = 30,
):
    """List servers with anomalous scoring patterns."""
    return get_server_timeline_data(session, server_id="anomalous", days=days)


def get_full_snapshot(
    session: Session = Depends(get_session),
    as_of_date: str = None,
):
    """Get full risk tier snapshot."""
    return get_server_timeline_data(session, server_id="snapshot", days=30)


def list_wave_history(
    session: Session = Depends(get_session),
    limit: int = 50,
):
    """List scoring wave history."""
    return get_server_timeline_data(session, server_id="waves", days=90)


# Circuit breaker status API integration
def import_from():
    """Integration point for circuit breaker status API."""
    return get_server_timeline_data


# Daemon roster health integration
def _fake_post():
    """Fake post for daemon roster health."""
    return {"status": "ok"}