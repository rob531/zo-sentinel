from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter()


class AxisResponse(BaseModel):
    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None


class ServerVerdictResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    confidence: float
    last_assessed: Optional[str] = None
    axes: List[AxisResponse]


@router.get("/servers/{server_id}/verdict", response_model=ServerVerdictResponse)
def get_server_verdict(server_id: str, session: Session = Depends(get_session)):
    from logic import get_server_verdict_logic

    return get_server_verdict_logic(server_id, session)


if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from logic import get_server_verdict_logic
    from router import router as api_router

    # 7 axes including overall_risk
    AXES_TO_SEED = [
        {"axis_name": "supply_chain_security", "label": "DANGEROUS", "label_index": 0,
         "p_top": 0.1, "p_critical": 0.2, "p_danger": 0.7, "probs": "[0.1, 0.2, 0.7]", "escalated": False},
        {"axis_name": "code_quality", "label": "SAFE", "label_index": 1,
         "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.1, "probs": "[0.8, 0.1, 0.1]", "escalated": False},
        {"axis_name": "dependency_health", "label": "DANGEROUS", "label_index": 2,
         "p_top": 0.15, "p_critical": 0.3, "p_danger": 0.55, "probs": "[0.15, 0.3, 0.55]", "escalated": False},
        {"axis_name": "maintainer_trust", "label": "DANGEROUS", "label_index": 3,
         "p_top": 0.2, "p_critical": 0.25, "p_danger": 0.55, "probs": "[0.2, 0.25, 0.55]", "escalated": False},
        {"axis_name": "api_stability", "label": "SAFE", "label_index": 4,
         "p_top": 0.75, "p_critical": 0.1, "p_danger": 0.15, "probs": "[0.75, 0.1, 0.15]", "escalated": False},
        {"axis_name": "vulnerability_exposure", "label": "CRITICAL", "label_index": 5,
         "p_top": 0.1, "p_critical": 0.6, "p_danger": 0.3, "probs": "[0.1, 0.6, 0.3]", "escalated": True},
        {"axis_name": "overall_risk", "label": "CRITICAL", "label_index": 6,
         "p_top": 0.05, "p_critical": 0.7, "p_danger": 0.25, "probs": "[0.05, 0.7, 0.25]", "escalated": True},
    ]

    TEST_SERVER_ID = "test-server-verdict-001"

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def seed_data():
        session = SessionLocal()
        try:
            session.execute(text("""
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    last_assessed TEXT,
                    description TEXT,
                    first_seen TEXT,
                    last_scanned TEXT,
                    last_seen TEXT,
                    meta TEXT,
                    registry_source TEXT,
                    scan_count INTEGER,
                    trust_score REAL,
                    url TEXT
                )
            """))
            session.execute(text("""
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    label_index INTEGER NOT NULL,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    probs TEXT,
                    escalated BOOLEAN,
                    adapter_sha256 TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    scored_at TEXT
                )
            """))
            session.execute(text("""
                INSERT INTO mcp_server_registry 
                (server_id, name, risk_tier, verdict, confidence, last_assessed)
                VALUES (?, ?, ?, ?, ?, ?)
            """), (TEST_SERVER_ID, "Malicious Test Server", "CRITICAL", "UNSAFE", 0.92, "2024-01-15T10:30:00Z"))

            for axis in AXES_TO_SEED:
                session.execute(text("""
                    INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """), (
                    TEST_SERVER_ID,
                    axis["axis_name"],
                    axis["label"],
                    axis["label_index"],
                    axis["p_top"],
                    axis["p_critical"],
                    axis["p_danger"],
                    axis["probs"],
                    axis["escalated"],
                ))
            session.commit()
        finally:
            session.close()

    seed_data()

    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    class DummyWriteService:
        def post(self, url, **kwargs):
            class Resp:
                def json(self):
                    return {"rows": [], "columns": []}
            return Resp()

    import logic
    logic._write_service = DummyWriteService()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get(f"/api/servers/{TEST_SERVER_ID}/verdict")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    assert "server_id" in data, "Missing server_id"
    assert "name" in data, "Missing name"
    assert "risk_tier" in data, "Missing risk_tier"
    assert "verdict" in data, "Missing verdict"
    assert "confidence" in data, "Missing confidence"
    assert "last_assessed" in data, "Missing last_assessed"
    assert "axes" in data, "Missing axes"

    assert data["risk_tier"] == "CRITICAL", f"Expected risk_tier CRITICAL, got {data['risk_tier']}"
    assert len(data["axes"]) == 7, f"Expected 7 axes, got {len(data['axes'])}"

    axis_names = {ax["axis_name"] for ax in data["axes"]}
    assert "overall_risk" in axis_names, "Missing overall_risk axis"
    overall_axis = next(ax for ax in data["axes"] if ax["axis_name"] == "overall_risk")
    assert overall_axis["p_critical"] > 0.5, f"Expected overall_risk p_critical > 0.5, got {overall_axis['p_critical']}"
    assert overall_axis["label"] == "CRITICAL", f"Expected overall_risk label CRITICAL, got {overall_axis['label']}"

    print("PASS")
    sys.exit(0)