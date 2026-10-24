from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_escalation_targets(db: Session, days: int = 30) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    statement = (
        select(
            McpLlmAxisScore,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    )
    rows = db.execute(statement).all()

    grouped: dict[tuple[str, str | None], dict[str, Any]] = {}
    servers: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for score, name, risk_tier in rows:
        group_key = (score.axis_name, score.escalated_to)
        group = grouped.setdefault(
            group_key,
            {"count": 0, "server_ids": set(), "label": score.label},
        )
        group["count"] += 1
        group["server_ids"].add(score.server_id)

        server_key = (score.server_id, score.axis_name, score.escalated_to)
        servers[server_key] = {
            "server_id": score.server_id,
            "name": name,
            "risk_tier": risk_tier,
            "escalated_to": score.escalated_to,
            "axis_name": score.axis_name,
        }

    by_axis = [
        {
            "axis_name": axis_name,
            "label": group["label"],
            "escalated_to": escalated_to,
            "count": group["count"],
            "distinct_servers": len(group["server_ids"]),
        }
        for (axis_name, escalated_to), group in sorted(
            grouped.items(),
            key=lambda entry: tuple(
                "" if value is None else str(value) for value in entry[0]
            ),
        )
    ]
    server_items = [
        servers[key]
        for key in sorted(
            servers,
            key=lambda values: tuple(
                "" if value is None else str(value) for value in values
            ),
        )
    ]

    return {
        "days": days,
        "total_escalations": len(rows),
        "by_axis": by_axis,
        "servers": server_items,
    }


if __name__ == "__main__":
    import sys

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    class AxisEscalationSummary(BaseModel):
        axis_name: str
        label: str | None = None
        escalated_to: str | None = None
        count: int
        distinct_servers: int

    class AxisEscalationServer(BaseModel):
        server_id: str
        name: str | None = None
        risk_tier: str | None = None
        escalated_to: str | None = None
        axis_name: str

    class AxisEscalationTargetsResponse(BaseModel):
        days: int
        total_escalations: int
        by_axis: list[AxisEscalationSummary]
        servers: list[AxisEscalationServer]

    try:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        McpServerRegistry.__table__.create(engine, checkfirst=True)
        McpLlmAxisScore.__table__.create(engine, checkfirst=True)
        TestingSession = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with TestingSession() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="contract-server-1",
                        name="Contract Server One",
                        risk_tier="high",
                    ),
                    McpServerRegistry(
                        server_id="contract-server-2",
                        name="Contract Server Two",
                        risk_tier="medium",
                    ),
                    McpServerRegistry(
                        server_id="contract-server-3",
                        name="Contract Server Three",
                        risk_tier="low",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    McpLlmAxisScore(
                        id=1,
                        server_id="contract-server-1",
                        axis_name="auth_strength",
                        label="critical",
                        escalated=True,
                        escalated_to="analyst_review",
                        model_version="contract-model-v1",
                        scored_at=now - timedelta(days=1),
                    ),
                    McpLlmAxisScore(
                        id=2,
                        server_id="contract-server-2",
                        axis_name="auth_strength",
                        label="high",
                        escalated=True,
                        escalated_to="analyst_review",
                        model_version="contract-model-v1",
                        scored_at=now - timedelta(days=2),
                    ),
                    McpLlmAxisScore(
                        id=3,
                        server_id="contract-server-3",
                        axis_name="network_egress",
                        label="critical",
                        escalated=True,
                        escalated_to="policy_review",
                        model_version="contract-model-v1",
                        scored_at=now - timedelta(days=3),
                    ),
                ]
            )
            session.commit()

        def override_get_session():
            session = TestingSession()
            try:
                yield session
            finally:
                session.close()

        test_app = FastAPI()

        @test_app.get(
            "/api/axes/escalation-targets",
            response_model=AxisEscalationTargetsResponse,
        )
        def escalation_targets_endpoint(
            days: int = Query(default=30, ge=0),
            db: Session = Depends(get_session),
        ) -> AxisEscalationTargetsResponse:
            return get_escalation_targets(db, days=days)

        test_app.dependency_overrides[get_session] = override_get_session
        with TestClient(test_app) as client:
            response = client.get(
                "/api/axes/escalation-targets",
                params={"days": 30},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 30, payload
            assert payload["total_escalations"] == 3, payload
            assert payload["by_axis"], payload
            assert "auth_strength" in {
                item["axis_name"] for item in payload["by_axis"]
            }, payload
            assert len(payload["servers"]) == 3, payload
        engine.dispose()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)

    print("PASS")
    sys.exit(0)