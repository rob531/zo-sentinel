from typing import Optional
import threading
import time
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60


class AuthStrengthResponse(BaseModel):
    server_id: str
    axis_name: str = "auth_strength"
    p_top: float
    p_critical: float
    p_danger: float
    probs: list[float]
    risk_tier: Optional[str] = None
    last_scored: Optional[datetime] = None


class ScoreResponse(BaseModel):
    server_id: str
    risk_tier: str
    last_assessed: datetime
    message: str


class HealthResponse(BaseModel):
    status: str
    axis_scored_count: int


class VerdictWrite(BaseModel):
    server_id: str
    risk_tier: str
    last_assessed: datetime


def compute_risk_tier(p_top: float) -> str:
    if p_top >= 0.7:
        return "TRUSTED"
    elif p_top >= 0.4:
        return "MODERATE_RISK"
    elif p_top >= 0.15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "CRITICAL"


def get_auth_strength_score(session, server_id: str) -> Optional[AuthStrengthResponse]:
    record = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "auth_strength",
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not record:
        return None

    registry = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )

    risk_tier = registry.risk_tier if registry else None
    last_scored = record.scored_at

    return AuthStrengthResponse(
        server_id=record.server_id,
        axis_name=record.axis_name,
        p_top=record.p_top,
        p_critical=record.p_critical,
        p_danger=record.p_danger,
        probs=record.probs or [],
        risk_tier=risk_tier,
        last_scored=last_scored,
    )


def rescore_server(session, server_id: str) -> tuple[str, datetime]:
    record = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "auth_strength",
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not record:
        raise ValueError(f"No auth_strength score found for server {server_id}")

    risk_tier = compute_risk_tier(record.p_top)
    last_assessed = datetime.utcnow()

    payload = {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "last_assessed": last_assessed.isoformat(),
    }
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={"table": "mcp_server_registry", "rows": [payload]},
        timeout=10,
    )
    resp.raise_for_status()

    return risk_tier, last_assessed


def get_axis_count() -> int:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT COUNT(*) as cnt FROM mcp_llm_axis_scores WHERE axis_name = 'auth_strength'"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        return rows[0]["cnt"] if rows else 0
    except Exception:
        return 0


def emit_heartbeat():
    try:
        payload = {
            "service": "auth_strength_scoring",
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
        }
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "service_health", "rows": [payload]},
            timeout=10,
        )
    except Exception:
        pass


def start_heartbeat():
    def loop():
        while True:
            emit_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


app = FastAPI(title="auth_strength_scoring")


@app.on_event("startup")
def startup():
    start_heartbeat()


@app.get("/servers/{server_id}/auth_strength", response_model=AuthStrengthResponse)
def get_auth_strength(
    server_id: str,
    session=Depends(get_session),
):
    result = get_auth_strength_score(session, server_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"No auth_strength score for {server_id}")
    return result


@app.post("/servers/{server_id}/score", response_model=ScoreResponse)
def score_server(
    server_id: str,
    session=Depends(get_session),
):
    try:
        risk_tier, last_assessed = rescore_server(session, server_id)
        return ScoreResponse(
            server_id=server_id,
            risk_tier=risk_tier,
            last_assessed=last_assessed,
            message="Rescored successfully",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/health", response_model=HealthResponse)
def health():
    count = get_axis_count()
    return HealthResponse(status="ok", axis_scored_count=count)


if __name__ == "__main__":
    import json
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=in_memory_engine)

    TestingSession = sessionmaker(bind=in_memory_engine)
    test_session = TestingSession()

    mock_axis_scores = []
    mock_registries = []

    class MockQuery:
        def __init__(self, model_class):
            self.model_class = model_class
            self._filters = []
            self._order = None

        def filter(self, *args):
            self._filters.append(args)
            return self

        def order_by(self, *args):
            self._order = args
            return self

        def first(self):
            if self.model_class == McpLlmAxisScore:
                for score in reversed(mock_axis_scores):
                    if score.axis_name == "auth_strength":
                        return score
            elif self.model_class == McpServerRegistry:
                for reg in reversed(mock_registries):
                    if reg.server_id:
                        return reg
            return None

    class MockSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def query(self, model_class):
            return MockQuery(model_class)

        def add(self, obj):
            if isinstance(obj, McpLlmAxisAxisScore):
                mock_axis_scores.append(obj)
            elif isinstance(obj, McpServerRegistry):
                mock_registries.append(obj)

        def commit(self):
            pass

    from unittest.mock import patch

    mock_time = datetime(2024, 1, 1, 12, 0, 0)

    class FakeAxisScore:
        def __init__(self, server_id, axis_name, p_top, p_critical, p_danger, probs, scored_at):
            self.server_id = server_id
            self.axis_name = axis_name
            self.p_top = p_top
            self.p_critical = p_critical
            self.p_danger = p_danger
            self.probs = probs
            self.scored_at = scored_at

    class FakeRegistry:
        def __init__(self, server_id, risk_tier=None):
            self.server_id = server_id
            self.risk_tier = risk_tier

    def test_get_session():
        return MockSession()

    mock_axis_scores.append(
        FakeAxisScore("test-auth-srv", "auth_strength", 0.72, 0.05, 0.02, [0.72, 0.23, 0.05], mock_time)
    )
    mock_registries.append(FakeRegistry("test-auth-srv", "TRUSTED"))

    mock_query_instance = MockQuery(McpLlmAxisScore)
    original_first = mock_query_instance.first

    def patched_first():
        for score in reversed(mock_axis_scores):
            if score.axis_name == "auth_strength":
                return score
        return None

    mock_query_instance.first = patched_first

    test_app = FastAPI()

    @test_app.get("/servers/{server_id}/auth_strength", response_model=AuthStrengthResponse)
    def test_get_auth_strength(server_id: str, session=Depends(test_get_session)):
        result = get_auth_strength_score(session, server_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"No auth_strength score for {server_id}")
        return result

    @test_app.post("/servers/{server_id}/score", response_model=ScoreResponse)
    def test_score_server(server_id: str, session=Depends(test_get_session)):
        try:
            risk_tier, last_assessed = rescore_server(session, server_id)
            return ScoreResponse(
                server_id=server_id,
                risk_tier=risk_tier,
                last_assessed=last_assessed,
                message="Rescored successfully",
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @test_app.get("/health", response_model=HealthResponse)
    def test_health():
        return HealthResponse(status="ok", axis_scored_count=len(mock_axis_scores))

    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    response = client.get("/servers/test-auth-srv/auth_strength")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["server_id"] == "test-auth-srv", f"Expected test-auth-srv, got {data['server_id']}"
    assert data["p_top"] == 0.72, f"Expected p_top 0.72, got {data['p_top']}"
    print("PASS: Test 1 - GET /servers/{server_id}/auth_strength returns correct data")

    mock_axis_scores.clear()
    mock_registries.clear()
    mock_axis_scores.append(
        FakeAxisScore("test-auth-srv", "auth_strength", 0.15, 0.30, 0.55, [0.15, 0.30, 0.55], mock_time)
    )
    mock_registries.append(FakeRegistry("test-auth-srv", "HIGH_RISK_ISOLATED"))

    response = client.get("/servers/test-auth-srv/auth_strength")
    assert response.status_code == 200
    data = response.json()
    computed_tier = compute_risk_tier(data["p_top"])
    assert computed_tier != "TRUSTED", f"Expected risk_tier not TRUSTED for p_top=0.15, got {computed_tier}"
    print("PASS: Test 2 - p_top=0.15 yields risk_tier not TRUSTED")

    print("PASS: All self-tests passed")