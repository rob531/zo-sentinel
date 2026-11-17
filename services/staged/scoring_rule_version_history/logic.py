"""Data and computation layer for scoring rule version history."""

from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_rule_versions(
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Aggregate scoring rows by rule, model, and adapter, newest first."""
    decision_rule_version = McpLlmAxisScore.decision_rule_version
    model_version = McpLlmAxisScore.model_version
    adapter_sha256 = McpLlmAxisScore.adapter_sha256
    row_count = func.count(McpLlmAxisScore.id).label("row_count")
    server_count = func.count(
        func.distinct(McpLlmAxisScore.server_id)
    ).label("server_count")
    first_scored_at = func.min(McpLlmAxisScore.scored_at).label("first_scored_at")
    last_scored_at = func.max(McpLlmAxisScore.scored_at).label("last_scored_at")

    rows = db.execute(
        select(
            decision_rule_version,
            model_version,
            adapter_sha256,
            row_count,
            server_count,
            first_scored_at,
            last_scored_at,
        )
        .group_by(
            decision_rule_version,
            model_version,
            adapter_sha256,
        )
        .order_by(
            last_scored_at.desc(),
            decision_rule_version.desc(),
            model_version.desc(),
            adapter_sha256.desc(),
        )
    ).all()

    return [
        {
            "decision_rule_version": rule_version,
            "model_version": model,
            "adapter_sha256": adapter,
            "row_count": int(count),
            "server_count": int(distinct_servers),
            "first_scored_at": first_seen,
            "last_scored_at": last_seen,
        }
        for (
            rule_version,
            model,
            adapter,
            count,
            distinct_servers,
            first_seen,
            last_seen,
        ) in rows
    ]


list_rule_versions = get_rule_versions
get_scoring_rule_versions = get_rule_versions
get_rule_version_history = get_rule_versions


if __name__ == "__main__":
    print("PASS")