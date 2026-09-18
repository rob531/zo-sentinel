from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, CadenceJobRun

from .logic import get_pipeline_health

router = APIRouter(prefix="/api/scoring")

@router.get("/pipeline-health", response_model=Dict[str, Any])
async def pipeline_health(
    session: Session = Depends(get_session)
) -> Dict[str, Any]:
    return get_pipeline_health(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Test setup
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Dependency override for testing
    test_app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Seed test data
    from datetime import datetime, timedelta
    from app.models import McpLlmAxisScore, McpServerRegistry, CadenceJobRun

    # Recent and stale scores
    recent_score = McpLlmAxisScore(
        server_id="server1",
        axis_name="test_axis",
        scored_at=datetime.utcnow(),
        label="test_label",
        label_index=1,
        probs=[0.1, 0.2, 0.7],
        p_danger=0.2,
        p_critical=0.1,
        p_top=0.7,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="test_sha",
    )
    stale_score = McpLlmAxisScore(
        server_id="server2",
        axis_name="test_axis",
        scored_at=datetime.utcnow() - timedelta(hours=48),
        label="test_label",
        label_index=1,
        probs=[0.1, 0.2, 0.7],
        p_danger=0.2,
        p_critical=0.1,
        p_top=0.7,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="test_sha",
    )
    pending_score = McpLlmAxisScore(
        server_id="server3",
        axis_name="test_axis",
        scored_at=None,
        label=None,
        label_index=None,
        probs=None,
        p_danger=None,
        p_critical=None,
        p_top=None,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="test_sha",
    )

    # Servers with last_seen vs last_scanned delta
    recent_server = McpServerRegistry(
        server_id="server1",
        last_seen=datetime.utcnow(),
        last_scanned=datetime.utcnow() - timedelta(hours=1),
        risk_tier="low",
        confidence=0.9,
        verdict="safe",
        verdict_reasoning="test_reasoning",
        trust_score=0.8,
        scan_count=1,
        registry_source="test_source",
        name="test_server1",
        url="http://test1.example.com",
        meta={"test": "data"},
    )
    stale_server = McpServerRegistry(
        server_id="server2",
        last_seen=datetime.utcnow() - timedelta(hours=48),
        last_scanned=datetime.utcnow() - timedelta(hours=49),
        risk_tier="medium",
        confidence=0.7,
        verdict="suspicious",
        verdict_reasoning="test_reasoning",
        trust_score=0.5,
        scan_count=2,
        registry_source="test_source",
        name="test_server2",
        url="http://test2.example.com",
        meta={"test": "data"},
    )

    # Scoring job run
    job_run = CadenceJobRun(
        job="scoring_pipeline",
        status="completed",
        started_at=datetime.utcnow() - timedelta(hours=1),
        finished_at=datetime.utcnow(),
        rows_affected=100,
        detail="test_detail",
    )

    # Add test data
    test_session = Session(test_engine)
    test_session.add_all([
        recent_score, stale_score, pending_score,
        recent_server, stale_server,
        job_run
    ])
    test_session.commit()

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    response = client.get("/api/scoring/pipeline-health")
    assert response.status_code == 200
    assert response.json()["pipeline_status"] == "HEALTHY"
    print("PASS")