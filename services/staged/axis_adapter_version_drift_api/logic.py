from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _build_queries(
    cutoff: datetime,
) -> tuple[Any, Any]:
    totals_query = (
        select(
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            func.count(McpLlmAxisScore.id).label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .where(
            McpLlmAxisScore.adapter_sha256.is_not(None),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.adapter_sha256)
        .order_by(McpLlmAxisScore.adapter_sha256.asc())
    )

    versions_query = (
        select(
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            McpLlmAxisScore.model_version.label("model_version"),
        )
        .where(
            McpLlmAxisScore.adapter_sha256.is_not(None),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.model_version,
        )
    )

    return totals_query, versions_query


def get_adapter_version_drift(
    days: int = 30,
    session: Session = Depends(get_session),
    db: Session | None = None,
) -> list[dict[str, Any]]:
    if db is not None:
        session = db

    if isinstance(days, Session):
        if isinstance(session, int):
            days, session = session, days
        else:
            session, days = days, 30

    if not isinstance(days, int):
        raise TypeError("days must be an integer")
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    totals_query, versions_query = _build_queries(cutoff)

    model_versions: dict[str, set[str]] = {}
    for adapter_sha256, model_version in session.execute(versions_query).all():
        if model_version is None:
            continue
        model_versions.setdefault(adapter_sha256, set()).add(str(model_version))

    adapters: list[dict[str, Any]] = []
    for row in session.execute(totals_query).all():
        adapter_sha256 = row.adapter_sha256
        versions = sorted(model_versions.get(adapter_sha256, set()))
        adapters.append(
            {
                "adapter_sha256": adapter_sha256,
                "rows": int(row.row_count),
                "servers": int(row.server_count),
                "model_versions": versions,
                "latest_scored_at": row.latest_scored_at,
                "drifted": len(versions) != 1,
            }
        )

    return adapters


def get_axis_adapter_version_drift(
    days: int = 30,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return get_adapter_version_drift(days=days, session=session)


def run_self_test() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    totals_query, versions_query = _build_queries(cutoff)
    totals_sql = str(totals_query.compile(dialect=postgresql.dialect()))
    versions_sql = str(versions_query.compile(dialect=postgresql.dialect()))

    assert "mcp_llm_axis_scores" in totals_sql
    assert "mcp_llm_axis_scores" in versions_sql
    assert "COUNT(DISTINCT" in totals_sql.upper()
    assert "MAX" in totals_sql.upper()
    assert "mcp_server_registry" not in totals_sql
    assert "mcp_server_registry" not in versions_sql

    print("PASS")


if __name__ == "__main__":
    run_self_test()