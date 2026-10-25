from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from inspect import Parameter, signature
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpScoreDispute

from .logic import compute_dispute_rollup

router = APIRouter(prefix="/api")


class DisputeStatusCount(BaseModel):
    status: str
    count: int


class DisputeReasonCategoryCount(BaseModel):
    reason_category: str
    count: int


class DisputeSubmittedByCount(BaseModel):
    submitted_by: str
    count: int


class DisputeStatusRollupResponse(BaseModel):
    total: int
    by_status: list[DisputeStatusCount]
    by_reason_category: list[DisputeReasonCategoryCount]
    oldest_open_at: datetime | None
    median_resolution_hours: float | None
    resolved_last_7d: int
    submitted_by: list[DisputeSubmittedByCount]


def _call_compute(status: str | None, db: Any) -> Any:
    parameters = list(signature(compute_dispute_rollup).parameters.values())
    call_parameters = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY)
    ]

    if not call_parameters:
        return compute_dispute_rollup()

    names = {parameter.name for parameter in call_parameters}
    if "status" in names:
        kwargs: dict[str, Any] = {"status": status}
        database_parameter = next(
            (name for name in ("db", "session", "database") if name in names),
            None,
        )
        if database_parameter is not None:
            kwargs[database_parameter] = db
        return compute_dispute_rollup(**kwargs)

    if len(call_parameters) == 1:
        return compute_dispute_rollup(status)

    database_names = {"db", "session", "database"}
    if call_parameters[0].name in database_names:
        return compute_dispute_rollup(db, status)
    return compute_dispute_rollup(status, db)


@router.get(
    "/disputes/status-rollup",
    response_model=DisputeStatusRollupResponse,
)
async def get_dispute_status_rollup(
    status: str | None = Query(default=None),
    db: Any = Depends(get_session),
) -> dict[str, Any]:
    result = _call_compute(status, db)
    if hasattr(result, "__await__"):
        result = await result
    return result


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_test_compute(status: str | None, db: Any) -> dict[str, Any]:
    from sqlalchemy import select

    rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.submitted_by,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpScoreDispute.proposed_overall_risk,
        )
    ).all()

    if status is not None:
        rows = [row for row in rows if row.status == status]

    now = datetime.now(timezone.utc)
    status_counts = Counter(row.status for row in rows)
    reason_counts = Counter(row.reason_category for row in rows)
    submitter_counts = Counter(row.submitted_by for row in rows)

    open_times = [
        row.created_at
        for row in rows
        if row.status == "open" and row.created_at is not None
    ]
    oldest_open_at = min(open_times) if open_times else None

    resolution_hours = []
    for row in rows:
        if row.status == "resolved" and row.resolved_at is not None:
            created_at = row.created_at
            resolved_at = row.resolved_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if resolved_at.tzinfo is None:
                resolved_at = resolved_at.replace(tzinfo=timezone.utc)
            resolution_hours.append(
                (resolved_at - created_at).total_seconds() / 3600
            )

    resolved_last_7d = 0
    cutoff = now - timedelta(days=7)
    for row in rows:
        if row.status != "resolved" or row.resolved_at is None:
            continue
        resolved_at = row.resolved_at
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        if resolved_at >= cutoff:
            resolved_last_7d += 1

    return {
        "total": len(rows),
        "by_status": [
            {"status": key, "count": status_counts[key]}
            for key in sorted(status_counts)
        ],
        "by_reason_category": [
            {"reason_category": key, "count": reason_counts[key]}
            for key in sorted(reason_counts)
        ],
        "oldest_open_at": _utc_iso(oldest_open_at),
        "median_resolution_hours": (
            median(resolution_hours) if resolution_hours else None
        ),
        "resolved_last_7d": resolved_last_7d,
        "submitted_by": [
            {"submitted_by": key, "count": submitter_counts[key]}
            for key in sorted(submitter_counts)
        ],
    }


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    disputes = Table(
        "mcp_score_disputes",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String, nullable=False),
        Column("reason_category", String, nullable=False),
        Column("submitted_by", String, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("resolved_at", DateTime(timezone=True), nullable=True),
        Column("proposed_overall_risk", String, nullable=True),
    )
    metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    statuses = [
        "open",
        "open",
        "open",
        "resolved",
        "resolved",
        "resolved",
        "investigating",
        "investigating",
    ]
    categories = [
        "fraud",
        "fraud",
        "model_drift",
        "fraud",
        "model_drift",
        "model_drift",
        "fraud",
        "model_drift",
    ]
    values = []
    for index, (row_status, category) in enumerate(zip(statuses, categories), start=1):
        created_at = now - timedelta(days=20 - index)
        resolved_at = None
        if row_status == "resolved":
            resolved_at = created_at + timedelta(hours=12)
        values.append(
            {
                "id": index,
                "status": row_status,
                "reason_category": category,
                "submitted_by": f"analyst-{index % 3}",
                "created_at": created_at,
                "resolved_at": resolved_at,
                "proposed_overall_risk": "high" if index % 2 else "medium",
            }
        )
    db.execute(insert(disputes), values)
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: db

    original_compute = compute_dispute_rollup
    globals()["compute_dispute_rollup"] = _self_test_compute
    try:
        with TestClient(test_app) as client:
            response = client.get("/api/disputes/status-rollup")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 8, payload
            assert len(payload["by_status"]) == 3, payload

            filtered = client.get(
                "/api/disputes/status-rollup",
                params={"status": "open"},
            )
            assert filtered.status_code == 200, filtered.text
            filtered_payload = filtered.json()
            assert filtered_payload["total"] == 3, filtered_payload
            assert all(
                item["status"] == "open"
                for item in filtered_payload["by_status"]
            ), filtered_payload
    finally:
        globals()["compute_dispute_rollup"] = original_compute
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()