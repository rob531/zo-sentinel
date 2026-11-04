"""Data and computation layer for the axis auth-strength consumer."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXIS_NAME = "auth_strength"


def get_axis_scores(db: Session, axis_name: str = AXIS_NAME) -> List[Dict[str, object]]:
    """Return the latest score for each server with a score on the requested axis."""
    ranked_scores = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.label.label("label"),
            McpLlmAxisScore.p_top.label("p_top"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("score_rank"),
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
        .subquery()
    )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            ranked_scores.c.label,
            ranked_scores.c.p_top,
            McpServerRegistry.risk_tier,
        )
        .join(ranked_scores, ranked_scores.c.server_id == McpServerRegistry.server_id)
        .where(ranked_scores.c.score_rank == 1)
        .order_by(McpServerRegistry.server_id)
    ).all()

    servers: List[Dict[str, object]] = []
    for server_id, name, label, p_top, risk_tier in rows:
        servers.append(
            {
                "server_id": server_id,
                "name": name,
                "label": label,
                "p_top": float(p_top) if p_top is not None else None,
                "risk_tier": risk_tier or "unknown",
            }
        )
    return servers


def get_auth_strength_scores(db: Session) -> List[Dict[str, object]]:
    """Return the latest auth-strength score for every scored server."""
    return get_axis_scores(db, AXIS_NAME)


def get_axis_distribution(
    db: Session, axis_name: str = AXIS_NAME
) -> Dict[str, int]:
    """Count the latest scores on an axis by label."""
    distribution: Dict[str, int] = {}
    for server in get_axis_scores(db, axis_name):
        label = str(server["label"] or "unknown")
        distribution[label] = distribution.get(label, 0) + 1
    return distribution


def get_auth_strength_distribution(db: Session) -> Dict[str, int]:
    """Count the latest auth-strength scores by label."""
    return get_axis_distribution(db, AXIS_NAME)


def get_risk_tier_counts(db: Session, axis_name: str = AXIS_NAME) -> Dict[str, int]:
    """Count servers with a score on an axis by registry risk tier."""
    counts: Dict[str, int] = {}
    for server in get_axis_scores(db, axis_name):
        tier = str(server["risk_tier"] or "unknown")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def get_auth_strength_data(
    db: Session,
) -> Dict[str, object]:
    """Return the server scores and label distribution for auth strength."""
    servers = get_auth_strength_scores(db)
    distribution: Dict[str, int] = {}
    for server in servers:
        label = str(server["label"] or "unknown")
        distribution[label] = distribution.get(label, 0) + 1
    return {"servers": servers, "distribution": distribution}


def get_axis_data(
    db: Session, axis_name: str = AXIS_NAME
) -> Dict[str, object]:
    """Return the server scores and label distribution for an axis."""
    servers = get_axis_scores(db, axis_name)
    distribution: Dict[str, int] = {}
    for server in servers:
        label = str(server["label"] or "unknown")
        distribution[label] = distribution.get(label, 0) + 1
    return {"servers": servers, "distribution": distribution}


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-a", name="Server A", risk_tier="low"),
                McpServerRegistry(server_id="server-b", name="Server B", risk_tier="medium"),
                McpServerRegistry(server_id="server-c", name="Server C", risk_tier="high"),
            ]
        )
        session.flush()
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name=AXIS_NAME,
                    model_version="test-model",
                    label="strong",
                    p_top=0.91,
                    scored_at=datetime(2026, 1, 1, 0, 0, 0),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-b",
                    axis_name=AXIS_NAME,
                    model_version="test-model",
                    label="weak",
                    p_top=0.22,
                    scored_at=datetime(2026, 1, 2, 0, 0, 0),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-c",
                    axis_name=AXIS_NAME,
                    model_version="test-model",
                    label="strong",
                    p_top=0.73,
                    scored_at=datetime(2026, 1, 3, 0, 0, 0),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/axis/auth-strength")
    def auth_strength_endpoint(session: Session = Depends(get_session)):
        return {
            "axis": AXIS_NAME,
            "label": AXIS_NAME,
            **get_auth_strength_data(session),
        }

    response = TestClient(that_app).get("/api/axis/auth-strength")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["servers"]) == 3
    assert payload["distribution"]["strong"] == 2
    assert payload["servers"][0]["p_top"] == 0.91
    print("PASS")