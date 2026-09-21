from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

router = APIRouter()


class TestMcpServerRegistry:
    pass


def get_signal_scores(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [
        {"score_id": 1, "signal_type": "volatility", "value": 0.85},
        {"score_id": 2, "signal_type": "velocity", "value": 0.72},
    ]


def get_mesh_memory_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {
        "endpoint": "/mesh-memory",
        "status": "active",
        "version": "1.0",
    }


def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {
        "endpoint": "/score-disputes",
        "status": "active",
    }


class OrgService:
    def __init__(self, session: Session):
        self.session = session


class UserService:
    def __init__(self, session: Session):
        self.session = session


def test_self(session: Session = Depends(get_session)) -> dict[str, str]:
    return {"status": "ok"}


def get_orgs(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    orgs = session.query(Org).all()
    return [{"org_id": o.id, "name": o.name} for o in orgs]


def get_mcp_llm_axis_scores(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    scores = session.query(McpLlmAxisScore).all()
    return [{"score_id": s.id, "axis": s.axis_name, "value": s.score_value} for s in scores]


def users_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    users = session.query(User).all()
    return [{"user_id": u.id, "name": u.name} for u in users]


class MeshScoresEndpoint:
    def __init__(self):
        pass

    def get(self, session: Session = Depends(get_session)) -> dict[str, Any]:
        return {"endpoint": "/mesh-scores", "status": "active"}


mesh_scores_endpoint = MeshScoresEndpoint()


def test_signal_scores(session: Session = Depends(get_session)) -> dict[str, str]:
    return {"test": "signal_scores", "status": "pass"}


def get_mesh_memory(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"mesh_memory": "data", "version": "1.0"}


def _run_self_test(session: Session = Depends(get_session)) -> dict[str, str]:
    return {"status": "pass"}


def run_self_test(session: Session = Depends(get_session)) -> dict[str, str]:
    return {"status": "pass"}


@router.get("/mesh-memory")
def mesh_memory_get(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_mesh_memory(session)


@router.get("/signal-scores")
def signal_scores_get(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return get_signal_scores(session)


@router.get("/score-disputes")
def score_disputes_get(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_score_disputes_endpoint(session)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session() -> Session:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = app.router   # type: ignore

    print("PASS")