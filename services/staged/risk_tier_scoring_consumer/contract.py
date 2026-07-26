from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
from fastapi.testclient import TestClient
from app.dependency_overrides import override_get_session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

app = FastAPI()

class RiskTierScoringResponse(BaseModel):
    servers_processed: int
    tiers: Dict[str, int]

class StatusResponse(BaseModel):
    last_run: Optional[str]
    servers_processed: int
    tiers: Dict[str, int]

def compute_risk_tier(p_top: float, escalated: bool) -> str:
    if escalated:
        return "HIGH_RISK_ISOLATED"
    if p_top > 75:
        return "TRUSTED_GENERAL"
    if p_top > 60:
        return "TRUSTED_RESEARCH"
    if p_top > 45:
        return "ENTERPRISE_CONTROLLED"
    if p_top > 30:
        return "CAUTION_LIMITED"
    if p_top > 15:
        return "HIGH_RISK_ISOLATED"
    return "KNOWN_THREAT"

def get_servers_with_all_axes(db: Session) -> List[int]:
    subquery = db.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name
    ).group_by(
        McpLlmAxisScore.server_id
    ).having(
        "COUNT(DISTINCT axis_name) = 7"
    ).subquery()

    return [row.server_id for row in db.query(subquery.c.server_id).all()]

def compute_composite_p_top(db: Session, server_id: int) -> float:
    scores = db.query(McpLlmAxisScore.p_top).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    return sum(score.p_top for score in scores) / len(scores)

def get_escalated_status(db: Session, server_id: int) -> bool:
    return db.query(McpLlmAxisScore.escalated).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.label == "CRITICAL"
    ).scalar() or False

@app.post("/api/scoring/consume", response_model=RiskTierScoringResponse)
async def consume_scoring(db: Session = Depends(get_session)):
    servers = get_servers_with_all_axes(db)
    processed = 0
    tiers = {
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0
    }

    for server_id in servers:
        p_top = compute_composite_p_top(db, server_id)
        escalated = get_escalated_status(db, server_id)
        tier = compute_risk_tier(p_top, escalated)

        db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).update({"risk_tier": tier})
        db.commit()

        tiers[tier] += 1
        processed += 1

    return {"servers_processed": processed, "tiers": tiers}

@app.get("/api/scoring/status", response_model=StatusResponse)
async def get_status(db: Session = Depends(get_session)):
    last_run = None
    processed = 0
    tiers = {
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0
    }

    # This is a simplified status check
    # In a real implementation, you might track last run time in a separate table
    return {"last_run": last_run, "servers_processed": processed, "tiers": tiers}

if __name__ == "__main__":
    # Set up test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = override_get_session(SessionLocal)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Seed test data
    test_db = SessionLocal()
    try:
        # Create test servers
        test_servers = [
            {"server_id": 1, "risk_tier": None},
            {"server_id": 2, "risk_tier": None},
            {"server_id": 3, "risk_tier": None},
            {"server_id": 4, "risk_tier": None},
            {"server_id": 5, "risk_tier": None}
        ]
        test_db.add_all([McpServerRegistry(**s) for s in test_servers])

        # Create test scores
        test_scores = [
            # Server 1 - all axes, high p_top
            {"server_id": 1, "axis_name": "axis1", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis2", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis3", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis4", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis5", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis6", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 1, "axis_name": "axis7", "p_top": 80, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},

            # Server 2 - all axes, CRITICAL escalated
            {"server_id": 2, "axis_name": "axis1", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis2", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis3", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis4", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis5", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis6", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 2, "axis_name": "axis7", "p_top": 70, "p_critical": 0, "p_danger": 0, "escalated": True, "label": "CRITICAL"},

            # Server 3 - missing some axes
            {"server_id": 3, "axis_name": "axis1", "p_top": 60, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 3, "axis_name": "axis2", "p_top": 60, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},

            # Server 4 - missing some axes
            {"server_id": 4, "axis_name": "axis1", "p_top": 50, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},

            # Server 5 - all axes, low p_top
            {"server_id": 5, "axis_name": "axis1", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis2", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis3", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis4", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis5", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis6", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"},
            {"server_id": 5, "axis_name": "axis7", "p_top": 10, "p_critical": 0, "p_danger": 0, "escalated": False, "label": "GOOD"}
        ]
        test_db.add_all([McpLlmAxisScore(**s) for s in test_scores])
        test_db.commit()
    finally:
        test_db.close()

    # Run test
    client = TestClient(app)
    response = client.post("/api/scoring/consume")
    assert response.status_code == 200
    assert response.json()["servers_processed"] >= 2

    # Verify CRITICAL server was marked HIGH_RISK_ISOLATED
    test_db = SessionLocal()
    try:
        server2 = test_db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == 2
        ).first()
        assert server2.risk_tier == "HIGH_RISK_ISOLATED"
    finally:
        test_db.close()

    print("PASS")