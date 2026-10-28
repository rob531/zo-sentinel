from __future__ import annotations

from statistics import median
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import ServerRegistry
except ImportError:
    from app.models import McpServerRegistry


_ESCALATION_QUERY = text(
    """
    SELECT
        scores.axis_name AS axis_name,
        scores.escalated_to AS escalated_to,
        registry.risk_tier AS risk_tier,
        scores.server_id AS server_id,
        scores.p_top AS p_top
    FROM mcp_llm_axis_scores AS scores
    INNER JOIN mcp_server_registry AS registry
        ON registry.server_id = scores.server_id
    WHERE scores.escalated_to IS NOT NULL
    """
)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aggregate_escalation_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    axis_name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    groups: dict[tuple[str | None, str | None], dict[str, Any]] = {}

    for row in rows:
        current_axis = row.get("axis_name")
        if axis_name is not None and current_axis != axis_name:
            continue

        escalated_to = row.get("escalated_to")
        if escalated_to is None:
            continue

        key = (current_axis, escalated_to)
        group = groups.setdefault(
            key,
            {
                "axis_name": current_axis,
                "escalated_to": escalated_to,
                "server_ids": set(),
                "matching_server_ids": set(),
                "matching_p_top": [],
            },
        )

        server_id = row.get("server_id")
        if server_id is not None:
            group["server_ids"].add(server_id)

        if row.get("risk_tier") == escalated_to:
            if server_id is not None:
                group["matching_server_ids"].add(server_id)
            p_top = _as_float(row.get("p_top"))
            if p_top is not None:
                group["matching_p_top"].append(p_top)

    result_rows: list[dict[str, Any]] = []
    for group in groups.values():
        matching_servers = len(group["matching_server_ids"])
        p_top_values = group["matching_p_top"]

        result_rows.append(
            {
                "axis_name": group["axis_name"],
                "escalated_to": group["escalated_to"],
                "tier_match_servers": matching_servers,
                "distinct_servers": len(group["server_ids"]),
                "median_p_top": median(p_top_values) if p_top_values else None,
            }
        )

    result_rows.sort(
        key=lambda item: (
            "" if item["axis_name"] is None else str(item["axis_name"]),
            "" if item["escalated_to"] is None else str(item["escalated_to"]),
        )
    )

    if limit is not None:
        result_rows = result_rows[: max(0, int(limit))]

    return {
        "total_axes": len(result_rows),
        "rows": result_rows,
    }


def get_escalation_to_tier(
    session: Session = get_session,
    axis_name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return escalation-to-tier statistics from the application database.

    The query intentionally uses only portable SQL constructs.  The median is
    calculated in Python after retrieving the relevant rows so that the same
    logic works with both PostgreSQL and the SQLite database used by the
    service contract test.
    """
    params: dict[str, Any] = {}
    query = _ESCALATION_QUERY

    if axis_name is not None:
        query = text(
            str(_ESCALATION_QUERY)
            + " AND scores.axis_name = :axis_name"
        )
        params["axis_name"] = axis_name

    result = session.execute(query, params)
    rows = result.mappings().all()
    return _aggregate_escalation_rows(rows, axis_name=axis_name, limit=limit)


def get_escalation_to_tier_statistics(
    session: Session = get_session,
    axis_name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for callers using the longer function name."""
    return get_escalation_to_tier(
        session=session,
        axis_name=axis_name,
        limit=limit,
    )


if __name__ == "__main__":
    _self_test_rows = [
        {
            "axis_name": "security",
            "escalated_to": "high",
            "risk_tier": "high",
            "server_id": "server-1",
            "p_top": 0.8,
        },
        {
            "axis_name": "security",
            "escalated_to": "high",
            "risk_tier": "high",
            "server_id": "server-1",
            "p_top": 0.6,
        },
        {
            "axis_name": "security",
            "escalated_to": "high",
            "risk_tier": "low",
            "server_id": "server-2",
            "p_top": 0.2,
        },
    ]
    _self_test_result = _aggregate_escalation_rows(_self_test_rows)
    assert _self_test_result["total_axes"] == 1
    assert _self_test_result["rows"][0]["tier_match_servers"] == 1
    assert _self_test_result["rows"][0]["distinct_servers"] == 2
    assert _self_test_result["rows"][0]["median_p_top"] == 0.7
    print("PASS")