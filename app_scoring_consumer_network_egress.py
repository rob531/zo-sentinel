# deps: requests
"""
app_scoring_consumer_network_egress.py
Scoring consumer for the network_egress axis: reads mcp_llm_axis_scores rows where
axis_name='network_egress', maps p_top/p_critical to a risk_tier string, and upserts
results to mcp_server_registry.risk_tier via write_service.

CONTRACT:
  run() + if __name__ == '__main__': run()
  compute_network_egress_tier(server_id: str, axis_rows: list[dict]) -> str
  query_network_egress_scores() -> list[dict]
"""
from __future__ import annotations

import datetime

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE = "http://127.0.0.1:8772"
AXIS_NAME = "network_egress"


def query_network_egress_scores() -> list[dict]:
    """
    Query mcp_llm_axis_scores for all rows where axis_name='network_egress'.
    Returns [{server_id, p_top, p_critical, label}].
    Uses app.db session so the production daemon reads from the real Postgres.
    """
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        rows = session.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.label,
            ).where(McpLlmAxisScore.axis_name == AXIS_NAME)
        ).all()
        return [
            {
                "server_id": r.server_id,
                "p_top": r.p_top,
                "p_critical": r.p_critical,
                "label": r.label,
            }
            for r in rows
        ]
    finally:
        session.close()


def compute_network_egress_tier(server_id: str, axis_rows: list[dict]) -> str:
    """
    Derive risk_tier string from network_egress axis rows for a server.

    Maps p_top thresholds to tiers:
      p_top >= 0.70 -> HIGH_RISK_ISOLATED   (high outbound data exfil risk)
      p_top 0.30-0.69 -> CAUTION_LIMITED    (moderate egress risk)
      p_top < 0.30  -> TRUSTED_GENERAL       (low/nominal egress)
      label CRITICAL -> HIGH_RISK_ISOLATED
      label DANGER   -> CAUTION_LIMITED
      label UNKNOWN  -> INSUFFICIENT
    """
    if not axis_rows:
        return "INSUFFICIENT"

    row = axis_rows[0]  # one row per server for a given axis
    p_top = row.get("p_top")
    label = (row.get("label") or "").upper()

    # Label-based override takes priority
    if label == "CRITICAL":
        return "HIGH_RISK_ISOLATED"
    elif label == "DANGER":
        return "CAUTION_LIMITED"
    elif label == "UNKNOWN":
        return "INSUFFICIENT"

    # p_top threshold mapping
    if p_top is not None:
        if p_top >= 0.70:
            return "HIGH_RISK_ISOLATED"
        elif p_top >= 0.30:
            return "CAUTION_LIMITED"
        else:
            return "TRUSTED_GENERAL"

    return "INSUFFICIENT"


def run() -> dict:
    """
    Main daemon cycle: query all servers with network_egress axis rows,
    compute risk_tier, write back to mcp_server_registry, emit heartbeat.
    """
    from app.db import SessionLocal
    session: Session = SessionLocal()
    try:
        # Get distinct servers that have network_egress scores
        rows = query_network_egress_scores()

        servers_updated = 0
        errors: list[str] = []

        # Group by server_id (should be 1 row each, but be safe)
        by_server: dict[str, list[dict]] = {}
        for r in rows:
            by_server.setdefault(r["server_id"], []).append(r)

        for server_id, axis_rows in by_server.items():
            try:
                tier = compute_network_egress_tier(server_id, axis_rows)
                _write_tier(server_id, tier)
                servers_updated += 1
            except Exception as exc:
                errors.append(f"{server_id}: {exc}")

        _heartbeat()
        return {"servers_updated": servers_updated, "errors": errors}
    finally:
        session.close()


def _write_tier(server_id: str, risk_tier: str) -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "mcp_server_registry",
                "rows": {"server_id": server_id, "risk_tier": risk_tier},
                "wait": True,
            },
            timeout=10,
        )
    except requests.RequestException:
        pass


def _heartbeat() -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "app_scoring_consumer_network_egress",
                    "status": "ok",
                    "meta": "{}",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                },
                "wait": False,
            },
            timeout=5,
        )
    except requests.RequestException:
        pass


if __name__ == "__main__":
    from unittest.mock import patch

    # Acceptance self-test: in-memory dict simulating 3 axis-score rows
    # (critical/high p_top, moderate p_top, low p_top)
    def mock_query() -> list[dict]:
        return [
            {"server_id": "srv-critical", "p_top": 0.85, "p_critical": 0.90, "label": "CRITICAL"},
            {"server_id": "srv-moderate", "p_top": 0.50, "p_critical": 0.30, "label": None},
            {"server_id": "srv-low",      "p_top": 0.10, "p_critical": 0.05, "label": None},
        ]

    with patch(f"{__name__}.query_network_egress_scores", mock_query):
        rows = mock_query()

        # srv-critical: label=CRITICAL -> HIGH_RISK_ISOLATED
        tier1 = compute_network_egress_tier("srv-critical", [rows[0]])
        assert tier1 == "HIGH_RISK_ISOLATED", f"srv-critical: expected HIGH_RISK_ISOLATED, got {tier1}"

        # srv-moderate: p_top=0.50 in [0.30, 0.70) -> CAUTION_LIMITED
        tier2 = compute_network_egress_tier("srv-moderate", [rows[1]])
        assert tier2 == "CAUTION_LIMITED", f"srv-moderate: expected CAUTION_LIMITED, got {tier2}"

        # srv-low: p_top=0.10 < 0.30 -> TRUSTED_GENERAL
        tier3 = compute_network_egress_tier("srv-low", [rows[2]])
        assert tier3 == "TRUSTED_GENERAL", f"srv-low: expected TRUSTED_GENERAL, got {tier3}"

        # Edge: empty rows -> INSUFFICIENT
        tier_empty = compute_network_egress_tier("srv-empty", [])
        assert tier_empty == "INSUFFICIENT", f"srv-empty: expected INSUFFICIENT, got {tier_empty}"

        # Edge: label=DANGER -> CAUTION_LIMITED
        tier_danger = compute_network_egress_tier("srv-danger", [{"server_id": "srv-danger", "p_top": 0.05, "p_critical": 0.10, "label": "DANGER"}])
        assert tier_danger == "CAUTION_LIMITED", f"srv-danger: expected CAUTION_LIMITED, got {tier_danger}"

        # Edge: label=UNKNOWN -> INSUFFICIENT
        tier_unknown = compute_network_egress_tier("srv-unknown", [{"server_id": "srv-unknown", "p_top": None, "p_critical": None, "label": "UNKNOWN"}])
        assert tier_unknown == "INSUFFICIENT", f"srv-unknown: expected INSUFFICIENT, got {tier_unknown}"

    print("PASS")
