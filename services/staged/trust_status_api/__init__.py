# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, User


class MeshMemoryEndpoint(BaseModel):
    endpoint_id: str
    endpoint_type: str = ""
    capabilities: list[str] = []


class MeshMemoryEndpointGet(BaseModel):
    endpoint_id: str
    endpoint_type: str = ""
    metadata: dict[str, Any] = {}


class SignalScoreResponse(BaseModel):
    axis: str
    score: float
    confidence: float
    factors: list[str] = []


class ScoreDisputeResponse(BaseModel):
    id: int
    axis: str
    original_score: float
    disputed_score: float
    reason: str
    status: str


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    org_id: int

    class Config:
        from_attributes = True


class TestMCPServerRegistry:
    def __init__(self) -> None:
        self.registry: dict[str, dict[str, Any]] = {}

    def register(self, server_id: str, config: dict[str, Any]) -> None:
        self.registry[server_id] = config

    def get(self, server_id: str) -> dict[str, Any] | None:
        return self.registry.get(server_id)

    def list_all(self) -> list[dict[str, Any]]:
        return list(self.registry.values())

    def unregister(self, server_id: str) -> bool:
        if server_id in self.registry:
            del self.registry[server_id]
            return True
        return False


_mesh_memory_store: dict[str, dict[str, Any]] = {}


def mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> list[MeshMemoryEndpoint]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "columns": ["endpoint_id", "endpoint_type", "capabilities"]},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json()
        return [MeshMemoryEndpoint(**r) for r in records]
    except requests.RequestException:
        return [MeshMemoryEndpoint(endpoint_id=k, **v) for k, v in _mesh_memory_store.items()]


def get_mesh_memory_by_id(
    endpoint_id: str,
    session: Session = Depends(get_session),
) -> MeshMemoryEndpointGet | None:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "filters": {"endpoint_id": endpoint_id}},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json()
        if records:
            return MeshMemoryEndpointGet(**records[0])
        return None
    except requests.RequestException:
        record = _mesh_memory_store.get(endpoint_id)
        if record:
            return MeshMemoryEndpointGet(endpoint_id=endpoint_id, **record)
        return None


def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> list[SignalScoreResponse]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "columns": ["axis", "score", "confidence", "factors"]},
            timeout=30,
        )
        response.raise_for_status()
        records = response.json()
        return [SignalScoreResponse(**r) for r in records]
    except requests.RequestException:
        return []


def users_endpoint(
    session: Session = Depends(get_session),
) -> list[UserRead]:
    stmt = select(User)
    results = session.execute(stmt).scalars().all()
    return [UserRead.model_validate(r) for r in results]


def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
) -> list[ScoreDisputeResponse]:
    stmt = select(McpScoreDispute)
    results = session.execute(stmt).scalars().all()
    return [ScoreDisputeResponse.model_validate(r) for r in results]


def run_self_test() -> bool:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/mesh-memory")
    def test_mesh_memory(session: Session = Depends(get_session)):
        return mesh_memory_endpoint(session)

    @test_app.get("/signal-scores")
    def test_signal_scores(session: Session = Depends(get_session)):
        return signal_scores_endpoint(session)

    @test_app.get("/users")
    def test_users(session: Session = Depends(get_session)):
        return users_endpoint(session)

    @test_app.get("/score-disputes")
    def test_score_disputes(session: Session = Depends(get_session)):
        return get_score_disputes_endpoint(session)

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    resp = client.get("/mesh-memory")
    assert resp.status_code == 200, f"mesh-memory failed: {resp.status_code}"
    resp = client.get("/signal-scores")
    assert resp.status_code == 200, f"signal-scores failed: {resp.status_code}"
    resp = client.get("/users")
    assert resp.status_code == 200, f"users failed: {resp.status_code}"
    resp = client.get("/score-disputes")
    assert resp.status_code == 200, f"score-disputes failed: {resp.status_code}"

    test_registry = TestMCPServerRegistry()
    test_registry.register("test-server", {"host": "localhost", "port": 8000})
    assert test_registry.get("test-server") is not None
    assert test_registry.list_all()
    test_registry.unregister("test-server")
    assert test_registry.get("test-server") is None

    print("PASS")
    return True


if __name__ == "__main__":
    run_self_test()