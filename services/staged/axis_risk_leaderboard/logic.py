"""Logic for the staged axis risk leaderboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXES = (
    "overall_risk",
    "security",
    "privacy",
    "reliability",
    "performance",
    "cost",
    "compliance",
)


def get_axes(db: Optional[Session] = None) -> List[str]:
    """Return the supported axis names."""
    return list(VALID_AXES)


def _axis_value(axis_name: Optional[str], axis: Optional[str]) -> str:
    value = axis if axis is not None else axis_name
    value = (value or "overall_risk").strip().lower()
    if value not in VALID_AXES:
        raise ValueError(
            "axis must be one of: " + ", ".join(VALID_AXES)
        )
    return value


def _number(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def get_axis_leaderboard(
    db: Session,
    axis_name: str = "overall_risk",
    limit: int = 25,
    *,
    axis: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the leaderboard from the latest score for each server."""
    selected_axis = _axis_value(axis_name, axis)
    try:
        selected_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    selected_limit = max(1, min(100, selected_limit))

    score = McpLlmAxisScore.__table__.alias("score")
    ranked = (
        select(
            score.c.server_id,
            score.c.p_top,
            score.c.p_critical,
            score.c.p_danger,
            score.c.escalated,
            score.c.escalated_to,
            score.c.scored_at,
            func.row_number()
            .over(
                partition_by=score.c.server_id,
                order_by=(score.c.scored_at.desc(), score.c.id.desc()),
            )
            .label("row_number"),
        )
        .where(score.c.axis_name == selected_axis)
        .subquery()
    )

    total = db.execute(
        select(func.count())
        .select_from(ranked)
        .where(ranked.c.row_number == 1)
    ).scalar_one()

    rows = db.execute(
        select(
            ranked.c.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            ranked.c.p_top,
            ranked.c.p_critical,
            ranked.c.p_danger,
            ranked.c.escalated,
            ranked.c.escalated_to,
            ranked.c.scored_at,
        )
        .select_from(
            ranked.outerjoin(
                McpServerRegistry,
                McpServerRegistry.server_id == ranked.c.server_id,
            )
        )
        .where(ranked.c.row_number == 1)
        .order_by(
            ranked.c.p_critical.desc(),
            ranked.c.p_top.desc(),
        )
        .limit(selected_limit)
    ).all()

    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "trust_score": _number(row.trust_score),
            "p_top": _number(row.p_top),
            "p_critical": _number(row.p_critical),
            "p_danger": _number(row.p_danger),
            "escalated": row.escalated,
            "escalated_to": row.escalated_to,
            "scored_at": row.scored_at,
        }
        for row in rows
    ]

    return {
        "axis": selected_axis,
        "servers": servers,
        "total": int(total or 0),
    }


def get_axis_risk_leaderboard(
    db: Session,
    axis_name: str = "overall_risk",
    limit: int = 25,
    *,
    axis: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the service-specific name."""
    return get_axis_leaderboard(db, axis_name, limit, axis=axis)


def _self_test() -> None:
    from fastapi import Depends, FastAPI, HTTPException, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    test_db = TestingSession()

    test_db.add_all(
        [
            McpServerRegistry(
                server_id="server-a",
                name="Server A",
                risk_tier="high",
                trust_score=0.2,
            ),
            McpServerRegistry(
                server_id="server-b",
                name="Server B",
                risk_tier="medium",
                trust_score=0.5,
            ),
            McpServerRegistry(
                server_id="server-c",
                name="Server C",
                risk_tier="low",
                trust_score=0.8,
            ),
        ]
    )
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-a",
                axis_name="overall_risk",
                model_version="test-model",
                p_top=0.4,
                p_critical=0.2,
                p_danger=0.3,
                escalated=False,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-b",
                axis_name="overall_risk",
                model_version="test-model",
                p_top=0.7,
                p_critical=0.5,
                p_danger=0.2,
                escalated=False,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-c",
                axis_name="overall_risk",
                model_version="test-model",
                p_top=0.6,
                p_critical=0.9,
                p_danger=0.1,
                escalated=True,
                escalated_to="security",
                scored_at=now,
            ),
        ]
    )
    test_db.commit()

    that_app = FastAPI()

    @that_app.get("/api/axis/leaderboard")
    def leaderboard(
        axis: str = Query("overall_risk"),
        limit: int = Query(25),
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        try:
            return get_axis_leaderboard(db, axis=axis, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def override_get_session():
        yield test_db

    that_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(that_app)

    response = client.get("/api/axis/leaderboard")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["servers"]) == 3
    assert payload["servers"][0]["server_id"] == "server-c"

    invalid = client.get("/api/axis/leaderboard", params={"axis": "not-an-axis"})
    assert invalid.status_code == 400
    print("PASS")


if __name__ == "__main__":
    _self_test()