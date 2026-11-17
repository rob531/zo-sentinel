from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _clamp_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(365, value))


def _version_key(
    decision_rule_version: str | None,
    model_version: str | None,
    adapter_sha256: str | None,
) -> tuple[str | None, str | None, str | None]:
    return (
        decision_rule_version,
        model_version,
        adapter_sha256,
    )


def get_version_drift(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict:
    days = _clamp_days(days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    grouped = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version.label(
                "decision_rule_version"
            ),
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            func.count(McpLlmAxisScore.id).label("row_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
        )
    ).all()

    ranked = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.decision_rule_version.label(
                "decision_rule_version"
            ),
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("server_rank"),
        )
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .subquery()
    )

    latest_rows = db.execute(
        select(ranked).where(ranked.c.server_rank == 1)
    ).all()

    total_rows = sum(int(row.row_count) for row in grouped)
    versions = [
        {
            "decision_rule_version": row.decision_rule_version,
            "model_version": row.model_version,
            "adapter_sha256": row.adapter_sha256,
            "rows": int(row.row_count),
            "first_scored_at": (
                row.first_scored_at.isoformat()
                if row.first_scored_at is not None
                else None
            ),
            "last_scored_at": (
                row.last_scored_at.isoformat()
                if row.last_scored_at is not None
                else None
            ),
            "share": (
                round(int(row.row_count) / total_rows, 3)
                if total_rows
                else 0.0
            ),
        }
        for row in grouped
    ]

    versions.sort(
        key=lambda item: (
            -item["rows"],
            tuple("" if value is None else str(value) for value in (
                item["decision_rule_version"],
                item["model_version"],
                item["adapter_sha256"],
            )),
        )
    )

    if versions:
        majority_version = versions[0]
        majority = {
            "decision_rule_version": majority_version[
                "decision_rule_version"
            ],
            "model_version": majority_version["model_version"],
            "adapter_sha256": majority_version["adapter_sha256"],
            "share": majority_version["share"],
        }
        majority_key = _version_key(
            majority["decision_rule_version"],
            majority["model_version"],
            majority["adapter_sha256"],
        )
    else:
        majority = {
            "decision_rule_version": None,
            "model_version": None,
            "adapter_sha256": None,
            "share": 0.0,
        }
        majority_key = _version_key(None, None, None)

    stale_server_count = sum(
        1
        for row in latest_rows
        if _version_key(
            row.decision_rule_version,
            row.model_version,
            row.adapter_sha256,
        )
        != majority_key
    )

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "versions": versions,
        "majority": majority,
        "stale_server_count": stale_server_count,
    }


def get_axis_scoring_version_drift(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict:
    return get_version_drift(db=db, days=days)


def get_scores_version_drift(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict:
    return get_version_drift(db=db, days=days)


def compute_version_drift(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict:
    return get_version_drift(db=db, days=days)


def compute_axis_scoring_version_drift(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict:
    return get_version_drift(db=db, days=days)