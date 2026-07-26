from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from services.staged.risk_tier_scoring_consumer.logic import compute_risk_tiers
from services.staged.risk_tier_scoring_consumer.write_service import execute_query

router = APIRouter()

def get_last_run_summary(session: Session) -> Dict:
    """Get summary of last scoring run."""
    servers = session.query(McpServerRegistry).all()
    tiers = {
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0
    }

    for server in servers:
        if server.risk_tier in tiers:
            tiers[server.risk_tier] += 1

    return {
        "servers_processed": len(servers),
        "tiers": tiers
    }

@router.post("/api/scoring/consume")
async def consume_scores(session: Session = Depends(get_session)):
    """Trigger risk tier scoring consumption."""
    try:
        result = compute_risk_tiers(session)
        return {"status": "success", "servers_processed": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/scoring/status")
async def get_status(session: Session = Depends(get_session)):
    """Get last run summary."""
    return get_last_run_summary(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Seed test data
    test_session = TestSession()
    try:
        # Seed 5 servers (2 with all axes, 1 CRITICAL-escalated, 2 missing axes)
        servers = [
            {"server_id": 1, "risk_tier": None},
            {"server_id": 2, "risk_tier": None},
            {"server_id": 3, "risk_tier": None},
            {"server_id": 4, "risk_tier": None},
            {"server_id": 5, "risk_tier": None}
        ]
        test_session.bulk_insert_mappings(McpServerRegistry, servers)

        axes = [
            {"server_id": 1, "axis_name": "axis1", "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label1"},
            {"server_id": 1, "axis_name": "axis2", "p_top": 0.7, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label2"},
            {"server_id": 1, "axis_name": "axis3", "p_top": 0.6, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label3"},
            {"server_id": 1, "axis_name": "axis4", "p_top": 0.5, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label4"},
            {"server_id": 1, "axis_name": "axis5", "p_top": 0.4, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label5"},
            {"server_id": 1, "axis_name": "axis6", "p_top": 0.3, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label6"},
            {"server_id": 1, "axis_name": "axis7", "p_top": 0.2, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label7"},
            {"server_id": 2, "axis_name": "axis1", "p_top": 0.9, "p_critical": 0.05, "p_danger": 0.02, "escalated": True, "label": "label1"},
            {"server_id": 2, "axis_name": "axis2", "p_top": 0.8, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label2"},
            {"server_id": 2, "axis_name": "axis3", "p_top": 0.7, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label3"},
            {"server_id": 2, "axis_name": "axis4", "p_top": 0.6, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label4"},
            {"server_id": 2, "axis_name": "axis5", "p_top": 0.5, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label5"},
            {"server_id": 2, "axis_name": "axis6", "p_top": 0.4, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label6"},
            {"server_id": 2, "axis_name": "axis7", "p_top": 0.3, "p_critical": 0.05, "p_danger": 0.02, "escalated": False, "label": "label7"},
            {"server_id": 3, "axis_name": "axis1", "p_top": 0.7, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label1"},
            {"server_id": 3, "axis_name": "axis2", "p_top": 0.6, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label2"},
            {"server_id": 3, "axis_name": "axis3", "p_top": 0.5, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label3"},
            {"server_id": 4, "axis_name": "axis1", "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label1"},
            {"server_id": 5, "axis_name": "axis1", "p_top": 0.9, "p_critical": 0.1, "p_danger": 0.05, "escalated": False, "label": "label1"}
        ]
        test_session.bulk_insert_mappings(McpLlmAxisScore, axes)
        test_session.commit()

        # Test POST /api/scoring/consume
        response = client.post("/api/scoring/consume")
        assert response.status_code == 200
        assert response.json()["servers_processed"] >= 2

        # Test GET /api/scoring/status
        response = client.get("/api/scoring/status")
        assert response.status_code == 200
        status = response.json()
        assert status["servers_processed"] >= 2
        assert status["tiers"]["HIGH_RISK_ISOLATED"] >= 1

        print("PASS")
    finally:
        test_session.close()