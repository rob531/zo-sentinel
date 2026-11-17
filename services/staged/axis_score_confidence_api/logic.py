"""logic.py -- axis confidence aggregation over the real application database."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _row_confidence(score: McpLlmAxisScore) -> float:
    """Return the highest axis probability for one score row."""
    values = [
        float(value)
        for value in (score.p_top, score.p_critical, score.p_danger)
        if value is not None
    ]
    return max(values) if values else 0.0


def get_axis_confidence(
    db: Session,
    axis_name: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Compute axis aggregates and the lowest-confidence servers.

    The query is intentionally expressed with the SQLAlchemy ORM so it works
    against the real app database and the contract's SQLite dependency
    override without relying on database-specific SQL.
    """
    query = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )
    if axis_name is not None:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)

    rows = db.execute(query).all()

    aggregate_values: Dict[str, Dict[str, Any]] = {}
    aggregate_rows: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    server_rows: Dict[str, Dict[str, Any]] = {}

    for score, server in rows:
        confidence = _row_confidence(score)
        axis = score.axis_name
        aggregate_rows[axis].append(
            {
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger,
                "confidence": confidence,
                "escalated": bool(score.escalated),
            }
        )

        server_key = str(server.server_id)
        server_info = server_rows.setdefault(
            server_key,
            {
                "server_id": server.server_id,
                "name": server.name,
                "risk_tier": server.risk_tier,
                "weakest_axis": axis,
                "confidence": confidence,
            },
        )
        if confidence < server_info["confidence"] or (
            confidence == server_info["confidence"]
            and axis < server_info["weakest_axis"]
        ):
            server_info["weakest_axis"] = axis
            server_info["confidence"] = confidence

    for axis, values in aggregate_rows.items():
        def mean(field: str) -> Optional[float]:
            numbers = [
                float(value[field])
                for value in values
                if value[field] is not None
            ]
            return sum(numbers) / len(numbers) if numbers else None

        aggregate_values[axis] = {
            "scored_count": len(values),
            "mean_p_top": mean("p_top"),
            "mean_p_critical": mean("p_critical"),
            "mean_p_danger": mean("p_danger"),
            "escalated_count": sum(1 for value in values if value["escalated"]),
            "low_confidence_count": sum(
                1 for value in values if value["confidence"] < 0.5
            ),
        }

    lowest_confidence_servers = sorted(
        server_rows.values(),
        key=lambda item: (
            item["confidence"],
            str(item["server_id"]),
        ),
    )[: max(0, int(limit))]

    return {
        "axis_aggregates": aggregate_values,
        "low_confidence_servers": lowest_confidence_servers,
    }


def get_axis_confidence_summary(
    db: Session,
    axis_name: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the summary naming convention."""
    return get_axis_confidence(db, axis_name=axis_name, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/axis/confidence")
    def axis_confidence(
        db: Session = Depends(get_session),
    ):
        return get_axis_confidence(db)

    seed_session = TestSession()
    try:
        seed_session.add_all(
            [
                McpServerRegistry(server_id="srv-1", name="one", risk_tier="low"),
                McpServerRegistry(server_id="srv-2", name="two", risk_tier="medium"),
                McpServerRegistry(server_id="srv-3", name="three", risk_tier="high"),
                McpLlmAxisScore(
                    id=1,
                    server_id="srv-1",
                    axis_name="availability",
                    model_version="test",
                    p_top=0.9,
                    p_critical=0.8,
                    p_danger=0.7,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv-2",
                    axis_name="safety",
                    model_version="test",
                    p_top=0.6,
                    p_critical=0.5,
                    p_danger=0.4,
                    escalated=True,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv-3",
                    axis_name="privacy",
                    model_version="test",
                    p_top=0.2,
                    p_critical=0.2,
                    p_danger=0.2,
                ),
            ]
        )
        seed_session.commit()
    finally:
        seed_session.close()

    response = TestClient(test_app).get("/api/axis/confidence")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["axis_aggregates"]) == {
        "availability",
        "safety",
        "privacy",
    }
    assert payload["axis_aggregates"]["safety"]["escalated_count"] == 1
    assert any(
        server["server_id"] == "srv-3"
        for server in payload["low_confidence_servers"]
    )
    print("PASS")