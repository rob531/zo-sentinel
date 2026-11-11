from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Org

__all__ = [
    "get_org_portfolio",
    "get_org_portfolio_for_request",
    "get_organization_portfolio",
    "get_portfolio",
]


_ORG_PORTFOLIO_SQL = text(
    """
    SELECT
        o.id AS org_id,
        o.name AS org_name,
        r.server_id AS server_id,
        r.name AS server_name,
        r.risk_tier AS risk_tier,
        r.verdict AS verdict,
        r.trust_score AS trust_score,
        r.last_assessed AS last_assessed,
        r.last_seen AS last_seen,
        a.axis_name AS axis_name,
        a.p_top AS p_top,
        a.scored_at AS scored_at
    FROM orgs AS o
    LEFT JOIN mcp_server_registry AS r
        ON r.org_id = o.id
    LEFT JOIN mcp_llm_axis_scores AS a
        ON a.server_id = r.server_id
    WHERE o.id = :org_id
    GROUP BY
        o.id,
        o.name,
        r.server_id,
        r.name,
        r.risk_tier,
        r.verdict,
        r.trust_score,
        r.last_assessed,
        r.last_seen,
        a.axis_name,
        a.p_top,
        a.scored_at
    """
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_stale(value: datetime | None, cutoff: datetime) -> bool:
    timestamp = _as_utc(value)
    return timestamp is not None and timestamp < cutoff


def get_org_portfolio(session: Session, org_id: int) -> dict[str, Any]:
    """Return the server portfolio for one organization.

    All organization and server data is read through the application's real
    database session. The query performs no writes and binds the organization
    identifier as a query parameter.
    """
    rows = session.execute(
        _ORG_PORTFOLIO_SQL,
        {"org_id": org_id},
    ).mappings().all()

    if not rows:
        raise LookupError(f"Organization {org_id} was not found")

    first = rows[0]
    servers: dict[Any, dict[str, Any]] = {}
    axis_values: dict[str, list[float]] = {}
    seen_axis_rows: set[tuple[Any, str, float, Any]] = set()

    for row in rows:
        server_id = row["server_id"]
        if server_id is not None and server_id not in servers:
            servers[server_id] = {
                "risk_tier": row["risk_tier"],
                "trust_score": row["trust_score"],
                "last_assessed": row["last_assessed"],
                "last_seen": row["last_seen"],
            }

        axis_name = row["axis_name"]
        p_top = row["p_top"]
        if axis_name is None or p_top is None:
            continue

        numeric_p_top = float(p_top)
        axis_key = (
            server_id,
            str(axis_name),
            numeric_p_top,
            row["scored_at"],
        )
        if axis_key in seen_axis_rows:
            continue
        seen_axis_rows.add(axis_key)
        axis_values.setdefault(str(axis_name), []).append(numeric_p_top)

    tier_counts: dict[str, int] = {}
    trust_scores: list[float] = []

    for server in servers.values():
        risk_tier = server["risk_tier"]
        tier_name = str(risk_tier) if risk_tier is not None else "UNKNOWN"
        tier_counts[tier_name] = tier_counts.get(tier_name, 0) + 1

        trust_score = server["trust_score"]
        if trust_score is not None:
            trust_scores.append(float(trust_score))

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    stale_scanned_count = 0
    for server in servers.values():
        last_scan = server["last_seen"] or server["last_assessed"]
        if _is_stale(last_scan, cutoff):
            stale_scanned_count += 1

    mean_trust_score = (
        sum(trust_scores) / len(trust_scores)
        if trust_scores
        else None
    )
    axis_p_top = {
        axis_name: sum(values) / len(values)
        for axis_name, values in axis_values.items()
        if values
    }

    return {
        "org_id": first["org_id"],
        "org_name": first["org_name"],
        "total_servers": len(servers),
        "tier_counts": tier_counts,
        "mean_trust_score": mean_trust_score,
        "stale_scanned_count": stale_scanned_count,
        "axis_p_top": axis_p_top,
    }


def get_org_portfolio_for_request(
    org_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """FastAPI-compatible dependency wrapper for the portfolio query."""
    return get_org_portfolio(session, org_id)


def get_organization_portfolio(
    session: Session,
    org_id: int,
) -> dict[str, Any]:
    return get_org_portfolio(session, org_id)


def get_portfolio(
    session: Session,
    org_id: int,
) -> dict[str, Any]:
    return get_org_portfolio(session, org_id)


if __name__ == "__main__":
    print("PASS")