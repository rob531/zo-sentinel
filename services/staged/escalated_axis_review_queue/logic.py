"""Logic for the escalated axis review queue service."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _serialise_scored_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _serialise_probability(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def get_escalated_axis_reviews(db: Session) -> Dict[str, Any]:
    """Return the latest score for each server/axis when it is escalated."""
    ranked = (
        select(
            McpLlmAxisScore.id,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.scored_at,
            McpServerRegistry.name.label("server_name"),
            func.row_number()
            .over(
                partition_by=(
                    McpLlmAxisScore.server_id,
                    McpLlmAxisScore.axis_name,
                ),
                order_by=(
                    McpLlmAxisScore.scored_at.desc().nullslast(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .subquery()
    )

    rows = db.execute(
        select(ranked)
        .where(ranked.c.row_number == 1)
        .where(
            or_(
                ranked.c.escalated.is_(True),
                ranked.c.escalated_to.is_not(None),
            )
        )
        .order_by(
            ranked.c.scored_at.desc().nullslast(),
            ranked.c.server_id,
            ranked.c.axis_name,
        )
    ).all()

    items = []
    for row in rows:
        items.append(
            {
                "server_id": row.server_id,
                "server_name": row.server_name,
                "axis_name": row.axis_name,
                "label": row.label,
                "p_top": _serialise_probability(row.p_top),
                "p_critical": _serialise_probability(row.p_critical),
                "p_danger": _serialise_probability(row.p_danger),
                "escalated": bool(row.escalated) if row.escalated is not None else False,
                "escalated_to": row.escalated_to,
                "scored_at": _serialise_scored_at(row.scored_at),
            }
        )

    return {"items": items, "count": len(items)}


list_escalated_axis_reviews = get_escalated_axis_reviews
get_escalated_review_queue = get_escalated_axis_reviews


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_factory()

    db.add_all(
        [
            McpServerRegistry(server_id="server-1", name="Server One"),
            McpServerRegistry(server_id="server-2", name="Server Two"),
            McpServerRegistry(server_id="server-3", name="Server Three"),
        ]
    )
    db.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-1",
                axis_name="critical",
                model_version="test-model",
                label="critical",
                p_top=0.91,
                p_critical=0.91,
                p_danger=0.04,
                escalated=True,
                escalated_to="security-review",
                scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-2",
                axis_name="critical",
                model_version="test-model",
                label="critical",
                p_top=0.84,
                p_critical=0.84,
                p_danger=0.05,
                escalated=False,
                escalated_to="security-review",
                scored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-3",
                axis_name="critical",
                model_version="test-model",
                label="safe",
                p_top=0.10,
                p_critical=0.10,
                p_danger=0.01,
                escalated=False,
                escalated_to=None,
                scored_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    app = FastAPI()

    def override_get_session():
        yield db

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/axes/escalated-review")
    def escalated_axis_review():
        return get_escalated_axis_reviews(db)

    response = TestClient(app).get("/api/axes/escalated-review")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    assert payload["items"][0]["axis_name"]
    assert payload["items"][0]["escalated_to"]

    db.close()
    print("PASS")


if __name__ == "__main__":
    _self_test()