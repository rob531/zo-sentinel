import json
import sys
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import select, update
from sqlalchemy.orm import Session

# Application DB session and models
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# ----------------------------------------------------------------------
# Mapping from p_top label to risk tier
_LABEL_TO_TIER: Dict[str, str] = {
    "CRITICAL": "HIGH_RISK_ISOLATED",
    "DANGER": "CAUTION_LIMITED",
    "NOMINAL": "TRUSTED_GENERAL",
    "LOW": "TRUSTED_GENERAL",
    "SAFE": "TRUSTED_GENERAL",
    "UNKNOWN": "INSUFFICIENT",
}

# Severity order for fallback when overall_risk axis is missing
_SEVERITY_ORDER: List[str] = [
    "CRITICAL",
    "DANGER",
    "UNKNOWN",
    "NOMINAL",
    "LOW",
    "SAFE",
]

# ----------------------------------------------------------------------
def _severity_rank(label: str) -> int:
    """Return an integer representing the severity rank (lower is more severe)."""
    try:
        return _SEVERITY_ORDER.index(label.upper())
    except ValueError:
        # Unknown labels are treated as least severe
        return len(_SEVERITY_ORDER)


def query_axis_scores_for_server(server_id: str) -> List[Dict[str, Any]]:
    """
    Query mcp_llm_axis_scores for all axis rows belonging to ``server_id``.
    Returns a list of dictionaries with keys: ``axis_name``, ``label``, ``p_top``.
    """
    with get_session() as session:  # type: Session
        stmt = select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
        ).where(McpLlmAxisScore.server_id == server_id)

        rows = session.execute(stmt).all()
        return [
            {"axis_name": r.axis_name, "label": r.label, "p_top": r.p_top}
            for r in rows
        ]


def compute_server_tier(
    server_id: str, axis_rows: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Derive the risk tier for ``server_id`` based on its axis rows.

    If ``axis_rows`` is ``None``, the function will query the database
    via :func:`query_axis_scores_for_server`.

    The tier is determined by the ``p_top`` label of the ``overall_risk``
    axis when present; otherwise the most severe label across all axes
    is used.
    """
    if axis_rows is None:
        axis_rows = query_axis_scores_for_server(server_id)

    if not axis_rows:
        # No data – treat as insufficient information
        return "INSUFFICIENT"

    # Prefer the overall_risk axis if it exists
    overall = next(
        (row for row in axis_rows if row.get("axis_name") == "overall_risk"), None
    )
    if overall and overall.get("p_top"):
        label = overall["p_top"]
    else:
        # Fallback: pick the most severe label among all rows
        sorted_rows = sorted(
            axis_rows,
            key=lambda r: _severity_rank(r.get("p_top", "")),
        )
        label = sorted_rows[0].get("p_top", "")

    tier = _LABEL_TO_TIER.get(label.upper(), "INSUFFICIENT")
    return tier


def _heartbeat() -> None:
    """Emit a simple heartbeat to the write_service."""
    try:
        requests.post(
            "http://127.0.0.1:8772/write",
            json={"service": "app_scoring_consumer_overall", "status": "alive"},
            timeout=2,
        )
    except Exception:
        # Heartbeat failures should not abort the main workflow
        pass


def run() -> Dict[str, Any]:
    """
    Main entry point.

    - Reads distinct server IDs that have axis scores.
    - Computes a risk tier for each server.
    - Persists the tier back to ``mcp_server_registry.risk_tier``.
    - Returns a summary dict ``{'servers_updated': int, 'errors': list}``.
    """
    summary: Dict[str, Any] = {"servers_updated": 0, "errors": []}
    _heartbeat()

    try:
        with get_session() as session:  # type: Session
            # Get distinct server IDs that have axis scores
            server_ids_stmt = select(McpLlmAxisScore.server_id).distinct()
            server_ids = [row.server_id for row in session.execute(server_ids_stmt)]

            for srv_id in server_ids:
                try:
                    axis_rows = query_axis_scores_for_server(srv_id)
                    tier = compute_server_tier(srv_id, axis_rows)

                    # Upsert into McpServerRegistry
                    existing_stmt = select(McpServerRegistry).where(
                        McpServerRegistry.server_id == srv_id
                    )
                    existing = session.execute(existing_stmt).scalar_one_or_none()

                    if existing:
                        if existing.risk_tier != tier:
                            upd = (
                                update(McpServerRegistry)
                                .where(McpServerRegistry.server_id == srv_id)
                                .values(risk_tier=tier)
                            )
                            session.execute(upd)
                            summary["servers_updated"] += 1
                    else:
                        # Create a new registry entry
                        new_entry = McpServerRegistry(server_id=srv_id, risk_tier=tier)
                        session.add(new_entry)
                        summary["servers_updated"] += 1

                except Exception as exc:
                    summary["errors"].append(f"{srv_id}: {exc}")

            session.commit()
    except Exception as exc:
        summary["errors"].append(str(exc))

    return summary


# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Self‑test: monkey‑patch ``query_axis_scores_for_server`` with deterministic data
    def _mock_query_axis_scores_for_server(server_id: str) -> List[Dict[str, Any]]:
        if server_id == "test-srv-1":
            return [
                {"axis_name": "overall_risk", "label": "NOMINAL", "p_top": "NOMINAL"},
                {"axis_name": "auth_strength", "label": "NOMINAL", "p_top": "NOMINAL"},
                {"axis_name": "capability_breadth", "label": "DANGER", "p_top": "DANGER"},
            ]
        if server_id == "test-srv-2":
            return [
                {"axis_name": "overall_risk", "label": "CRITICAL", "p_top": "CRITICAL"},
                {"axis_name": "auth_strength", "label": "UNKNOWN", "p_top": "UNKNOWN"},
            ]
        return []

    # Replace the real DB query with the mock
    globals()["query_axis_scores_for_server"] = _mock_query_axis_scores_for_server

    # Perform assertions
    tier1 = compute_server_tier("test-srv-1")
    assert tier1 == "TRUSTED_GENERAL", f"Expected TRUSTED_GENERAL, got {tier1}"
    tier2 = compute_server_tier("test-srv-2")
    assert tier2 == "HIGH_RISK_ISOLATED", f"Expected HIGH_RISK_ISOLATED, got {tier2}"

    print("PASS")