from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _newer_score(candidate: McpLlmAxisScore, current: McpLlmAxisScore) -> bool:
    if candidate.scored_at is None:
        if current.scored_at is not None:
            return False
        return (candidate.id or 0) > (current.id or 0)

    if current.scored_at is None:
        return True

    if candidate.scored_at != current.scored_at:
        return candidate.scored_at > current.scored_at

    return (candidate.id or 0) > (current.id or 0)


def _empty_consensus(server_id: str) -> dict[str, Any]:
    return {
        "server_id": server_id,
        "server_name": None,
        "risk_tier": None,
        "axes": {},
        "latest_scored_at": None,
        "rule_version": None,
        "model_version": None,
        "axis_count": 0,
    }


def fetch_consensus(server_id: str, db: Any = Depends(get_session)) -> dict[str, Any]:
    server = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    if server is None:
        return _empty_consensus(server_id)

    rows = db.execute(
        select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    ).scalars().all()

    latest_by_axis: dict[str, McpLlmAxisScore] = {}
    for row in rows:
        if row.axis_name not in AXIS_NAMES:
            continue
        current = latest_by_axis.get(row.axis_name)
        if current is None or _newer_score(row, current):
            latest_by_axis[row.axis_name] = row

    axes: dict[str, dict[str, Any]] = {}
    for axis_name in AXIS_NAMES:
        row = latest_by_axis.get(axis_name)
        if row is None:
            continue
        axes[axis_name] = {
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "escalated": row.escalated,
            "escalated_to": row.escalated_to,
            "decision_rule_version": row.decision_rule_version,
            "model_version": row.model_version,
            "scored_at": row.scored_at,
        }

    latest_row = None
    for row in latest_by_axis.values():
        if latest_row is None or _newer_score(row, latest_row):
            latest_row = row

    return {
        "server_id": server_id,
        "server_name": server.name,
        "risk_tier": server.risk_tier,
        "axes": axes,
        "latest_scored_at": latest_row.scored_at if latest_row else None,
        "rule_version": latest_row.decision_rule_version if latest_row else None,
        "model_version": latest_row.model_version if latest_row else None,
        "axis_count": len(axes),
    }


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    older = now.replace(hour=max(0, now.hour - 1))
    score_id = 1

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Example Server One",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Example Server Two",
                    risk_tier="low",
                ),
            ]
        )

        for server_id in ("server-1", "server-2"):
            for axis_name in AXIS_NAMES:
                session.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=server_id,
                        axis_name=axis_name,
                        label="old",
                        p_top=0.1,
                        p_critical=0.1,
                        p_danger=0.1,
                        escalated=False,
                        escalated_to=None,
                        decision_rule_version="rule-old",
                        model_version="model-old",
                        scored_at=older,
                    )
                )
                score_id += 1

                if server_id == "server-1":
                    session.add(
                        McpLlmAxisScore(
                            id=score_id,
                            server_id=server_id,
                            axis_name=axis_name,
                            label="current",
                            p_top=0.91,
                            p_critical=0.22,
                            p_danger=0.33,
                            escalated=True,
                            escalated_to="security",
                            decision_rule_version="rule-current",
                            model_version="model-current",
                            scored_at=now,
                        )
                    )
                    score_id += 1

        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/scoring/consensus/{server_id}")
    def consensus_endpoint(server_id: str, db: Any = Depends(get_session)):
        result = fetch_consensus(server_id, db)
        if not result["axes"]:
            return JSONResponse(status_code=404, content=result)
        return result

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/consensus/server-1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["server_id"] == "server-1"
        assert payload["server_name"] == "Example Server One"
        assert payload["risk_tier"] == "high"
        assert len(payload["axes"]) == 7
        assert payload["axis_count"] == 7
        assert payload["axes"]["overall_risk"]["p_top"] == 0.91
        assert payload["axes"]["overall_risk"]["label"] == "current"
        assert payload["latest_scored_at"] is not None
        assert payload["rule_version"] == "rule-current"
        assert payload["model_version"] == "model-current"

        missing = client.get("/api/scoring/consensus/missing-server")
        assert missing.status_code == 404
        assert missing.json()["axes"] == {}

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()