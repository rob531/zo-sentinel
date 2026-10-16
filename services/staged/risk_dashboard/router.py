# services/staged/risk_dashboard/logic.py
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry, VulnLink, VulnAdvisory


class RiskDashboardSummary:
    def __init__(
        self,
        risk_tiers: List[dict],
        axis_means: List[dict],
        cve_breakdown: List[dict],
        unscored_backlog: int,
        scanned_at: str
    ):
        self.risk_tiers = risk_tiers
        self.axis_means = axis_means
        self.cve_breakdown = cve_breakdown
        self.unscored_backlog = unscored_backlog
        self.scanned_at = scanned_at

    def to_dict(self) -> dict:
        return {
            "risk_tiers": self.risk_tiers,
            "axis_means": self.axis_means,
            "cve_breakdown": self.cve_breakdown,
            "unscored_backlog": self.unscored_backlog,
            "scanned_at": self.scanned_at,
        }


def get_risk_tier_distribution(session: Session) -> List[dict]:
    """Count servers per risk_tier with percentage."""
    results = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    total = sum(r.count for r in results)
    return [
        {
            "tier": r.risk_tier or "unknown",
            "count": r.count,
            "pct": round((r.count / total) * 100, 2) if total > 0 else 0.0,
        }
        for r in results
    ]


def get_axis_score_rollup(session: Session) -> List[dict]:
    """Mean p_top per axis for servers scored in last 30 days."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    results = (
        session.query(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("mean_score"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )
    return [
        {"axis": r.axis_name, "mean_score": round(float(r.mean_score or 0), 4)}
        for r in results
    ]


def get_cve_severity_breakdown(session: Session) -> List[dict]:
    """Count CVE severities from vuln_links joined to vuln_advisories."""
    results = (
        session.query(
            VulnAdvisory.severity,
            func.count(VulnLink.id).label("count"),
        )
        .join(
            VulnLink,
            VulnLink.advisory_id == VulnAdvisory.id,
        )
        .group_by(VulnAdvisory.severity)
        .all()
    )
    return [
        {"severity": r.severity or "unknown", "count": r.count}
        for r in results
    ]


def get_unscored_backlog(session: Session) -> int:
    """Count servers with no axis scores in last 7 days."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    scored_server_ids = (
        session.query(McpLlmAxisScore.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .distinct()
    )
    backlog_count = (
        session.query(func.count(McpServerRegistry.server_id))
        .filter(~McpServerRegistry.server_id.in_(scored_server_ids))
        .scalar()
    )
    return backlog_count or 0


def compute_risk_dashboard_summary(session: Session) -> RiskDashboardSummary:
    """Compute full risk dashboard summary."""
    risk_tiers = get_risk_tier_distribution(session)
    axis_means = get_axis_score_rollup(session)
    cve_breakdown = get_cve_severity_breakdown(session)
    unscored_backlog = get_unscored_backlog(session)
    scanned_at = datetime.utcnow().isoformat() + "Z"

    return RiskDashboardSummary(
        risk_tiers=risk_tiers,
        axis_means=axis_means,
        cve_breakdown=cve_breakdown,
        unscored_backlog=unscored_backlog,
        scanned_at=scanned_at,
    )