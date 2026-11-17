"""Data and computation layer for dispute shift-impact reporting."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


def _serialise_timestamp(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _proposed_axis_values(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}

    if isinstance(value, list):
        value = {
            item.get("axis_name"): item.get("p_top")
            for item in value
            if isinstance(item, dict) and item.get("axis_name") is not None
        }

    if not isinstance(value, dict):
        return {}

    result: dict[str, float] = {}
    for axis_name, proposed in value.items():
        if isinstance(proposed, dict):
            proposed = proposed.get("p_top")
        if isinstance(proposed, bool) or not isinstance(proposed, (int, float)):
            continue
        result[str(axis_name)] = float(proposed)
    return result


def _compute_with_session(db: Session, status: str | None) -> dict[str, Any]:
    statement = (
        select(McpScoreDispute, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .order_by(McpScoreDispute.created_at, McpScoreDispute.id)
    )
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    dispute_rows = db.execute(statement).all()
    server_ids = {row.server_id for row, _ in dispute_rows if row.server_id}

    scores_by_server: dict[str, list[McpLlmAxisScore]] = {}
    if server_ids:
        score_rows = db.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id.in_(server_ids))
            .order_by(McpLlmAxisScore.scored_at, McpLlmAxisScore.id)
        ).scalars()
        for score in score_rows:
            scores_by_server.setdefault(score.server_id, []).append(score)

    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    shifts: list[dict[str, Any]] = []

    for dispute, registry in dispute_rows:
        dispute_status = dispute.status or "unknown"
        reason_category = dispute.reason_category or "unknown"
        status_counts[dispute_status] += 1
        reason_counts[reason_category] += 1

        proposed_axes = _proposed_axis_values(dispute.proposed_axes)
        axis_deltas: list[dict[str, Any]] = []

        for score in scores_by_server.get(dispute.server_id, []):
            current_p_top = float(score.p_top) if score.p_top is not None else None
            proposed_p_top = proposed_axes.get(score.axis_name)
            delta = (
                proposed_p_top - current_p_top
                if proposed_p_top is not None and current_p_top is not None
                else None
            )
            axis_deltas.append(
                {
                    "axis_name": score.axis_name,
                    "current_p_top": current_p_top,
                    "proposed_p_top": proposed_p_top,
                    "delta": delta,
                }
            )

        shifts.append(
            {
                "dispute_id": dispute.id,
                "server_id": dispute.server_id,
                "server_name": registry.name if registry is not None else None,
                "current_tier": registry.risk_tier if registry is not None else None,
                "proposed_tier": dispute.proposed_overall_risk,
                "reason_category": reason_category,
                "status": dispute_status,
                "axis_deltas": axis_deltas,
                "created_at": _serialise_timestamp(dispute.created_at),
            }
        )

    return {
        "total": len(dispute_rows),
        "by_status": dict(status_counts),
        "by_reason_category": dict(reason_counts),
        "shifts": shifts,
    }


def compute_dispute_shift_impact(
    status: str | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    """Compute proposed-versus-current risk-tier shifts for score disputes."""
    if isinstance(status, Session):
        status, db = db, status  # type: ignore[assignment]

    if db is not None:
        return _compute_with_session(db, status)

    provider = get_session()
    owned_session: Session | None = None
    close_provider = False

    try:
        if isinstance(provider, Session):
            owned_session = provider
        else:
            owned_session = next(provider)
            close_provider = True
        return _compute_with_session(owned_session, status)
    finally:
        if close_provider:
            close_provider = getattr(provider, "close", None)
            if callable(close_provider):
                close_provider()
        elif owned_session is not None:
            owned_session.close()


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            McpServerRegistry.__table__,
            McpLlmAxisScore.__table__,
            McpScoreDispute.__table__,
        ],
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    with test_session_factory() as db:
        db.add(
            McpServerRegistry(
                server_id="server-1",
                name="Example Server",
                risk_tier="medium",
            )
        )
        db.add(
            McpLlmAxisScore(
                id=1,
                server_id="server-1",
                axis_name="malicious",
                model_version="test-model",
                p_top=0.2,
                p_critical=0.05,
                label="malicious",
            )
        )
        db.add(
            McpLlmAxisScore(
                id=2,
                server_id="server-1",
                axis_name="data_exposure",
                model_version="test-model",
                p_top=0.3,
                p_critical=0.1,
                label="data_exposure",
            )
        )
        db.add(
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="analyst-1",
                proposed_overall_risk="high",
                proposed_axes={"malicious": 0.8},
                reason_category="under_scored",
                explanation="Pending analyst dispute.",
                status="pending",
            )
        )
        db.add(
            McpScoreDispute(
                id=2,
                server_id="server-1",
                submitted_by="analyst-2",
                proposed_overall_risk="low",
                proposed_axes={"malicious": 0.1},
                reason_category="over_scored",
                explanation="Resolved analyst dispute.",
                status="resolved",
            )
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/disputes/shift-impact")
    def shift_impact(
        status: str | None = None,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return compute_dispute_shift_impact(status=status, db=db)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/disputes/shift-impact")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["by_status"] == {"pending": 1, "resolved": 1}
    assert payload["by_reason_category"] == {"under_scored": 1, "over_scored": 1}
    assert any(
        shift["proposed_tier"] == "high" and shift["status"] == "pending"
        for shift in payload["shifts"]
    )
    print("PASS")


if __name__ == "__main__":
    _self_test()