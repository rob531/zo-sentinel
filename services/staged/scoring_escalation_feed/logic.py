"""logic.py -- scoring escalation feed data layer.

Provides read-only access to escalated LLM axis scores from the authoritative
application database. The router remains responsible for HTTP/Pydantic concerns.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _clamp_days(days: int) -> int:
    """Clamp the requested lookback window to the supported range."""
    return max(1, min(365, int(days)))


def get_escalations(
    db: Session,
    days: int = 30,
    axis_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return escalated axis scores with server display information.

    Only scores marked as escalated and scored within the requested lookback
    window are returned. The registry join is a left join so an escalated score
    remains visible if its registry record is missing.
    """
    cutoff = datetime.utcnow() - timedelta(days=_clamp_days(days))

    query = (
        select(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name.label("server_name"),
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.scored_at,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
    )

    if axis_name:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)

    rows = db.execute(query).all()
    return [
        {
            "server_id": row.server_id,
            "server_name": row.server_name,
            "axis_name": row.axis_name,
            "p_critical": row.p_critical,
            "escalated_to": row.escalated_to,
            "scored_at": row.scored_at,
            "risk_tier": row.risk_tier,
        }
        for row in rows
    ]


# Compatibility aliases for callers that use a list/fetch naming convention.
list_escalations = get_escalations
fetch_escalations = get_escalations


def _self_test() -> None:
    """Exercise the feed against a local in-memory SQLite contract store."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    now = datetime.utcnow()
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-alpha",
                name="Alpha",
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="server-beta",
                name="Beta",
                risk_tier="critical",
            ),
            McpServerRegistry(
                server_id="server-gamma",
                name="Gamma",
                risk_tier="low",
            ),
            McpLlmAxisScore(
                id=1,
                server_id="server-alpha",
                axis_name="authentication",
                label="Authentication",
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.3,
                escalated=True,
                escalated_to="critical",
                model_version="test-model",
                scored_at=now - timedelta(days=1),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-beta",
                axis_name="network_exposure",
                label="Network Exposure",
                p_top=0.1,
                p_critical=0.4,
                p_danger=0.2,
                escalated=True,
                escalated_to="danger",
                model_version="test-model",
                scored_at=now - timedelta(days=2),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-gamma",
                axis_name="authentication",
                label="Authentication",
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.1,
                escalated=False,
                model_version="test-model",
                scored_at=now - timedelta(days=1),
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scoring/escalations")
    def scoring_escalations(
        days: int = Query(30),
        axis: Optional[str] = Query(None),
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return {
            "days": _clamp_days(days),
            "escalations": get_escalations(session, days=days, axis_name=axis),
        }

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session
    try:
        response = TestClient(test_app).get(
            "/api/scoring/escalations",
            params={"days": 30, "axis": "authentication"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert len(payload["escalations"]) == 1
        assert payload["escalations"][0]["escalated_to"] == "critical"

        response = TestClient(test_app).get(
            "/api/scoring/escalations",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["escalations"]) == 2
        assert any(
            item["escalated_to"] == "critical" for item in payload["escalations"]
        )
        assert all(item["server_id"] != "server-gamma" for item in payload["escalations"])
    finally:
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()