from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import requests
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI()

class DerivationRequest(BaseModel):
    server_id: Optional[str] = None

class DerivationResponse(BaseModel):
    derived: int
    updated: int
    skipped: int

def calculate_risk_tier(composite_score: float) -> str:
    if composite_score > 75:
        return "TRUSTED_GENERAL"
    elif composite_score > 60:
        return "TRUSTED_RESEARCH"
    elif composite_score > 45:
        return "ENTERPRISE_CONTROLLED"
    elif composite_score > 30:
        return "CAUTION_LIMITED"
    elif composite_score > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def get_axis_scores(db: Session, server_id: str) -> List[McpLlmAxisScore]:
    return db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).order_by(McpLlmAxisScore.label_index).all()

def update_risk_tier(db: Session, server_id: str, risk_tier: str) -> bool:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if server:
        if server.risk_tier != risk_tier:
            server.risk_tier = risk_tier
            db.commit()
            return True
    return False

@app.post("/api/scoring/derive", response_model=DerivationResponse)
async def derive_risk_tiers(request: DerivationRequest, db: Session = Depends(get_session)) -> DerivationResponse:
    derived = 0
    updated = 0
    skipped = 0

    if request.server_id:
        servers = [request.server_id]
    else:
        servers = [server.server_id for server in db.query(McpServerRegistry).all()]

    for server_id in servers:
        axis_scores = get_axis_scores(db, server_id)
        if len(axis_scores) == 7:
            composite_score = sum(score.p_top for score in axis_scores) / 7
            risk_tier = calculate_risk_tier(composite_score)
            if update_risk_tier(db, server_id, risk_tier):
                updated += 1
            derived += 1
        else:
            skipped += 1

    return DerivationResponse(derived=derived, updated=updated, skipped=skipped)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    test_db = SessionLocal()
    test_servers = [
        {"server_id": "s1", "risk_tier": None},
        {"server_id": "s2", "risk_tier": None},
        {"server_id": "s3", "risk_tier": None}
    ]
    test_scores = [
        {"server_id": "s1", "label_index": 0, "p_top": 0.9},
        {"server_id": "s1", "label_index": 1, "p_top": 0.8},
        {"server_id": "s1", "label_index": 2, "p_top": 0.8},
        {"server_id": "s1", "label_index": 3, "p_top": 0.8},
        {"server_id": "s1", "label_index": 4, "p_top": 0.8},
        {"server_id": "s1", "label_index": 5, "p_top": 0.8},
        {"server_id": "s1", "label_index": 6, "p_top": 0.8},
        {"server_id": "s2", "label_index": 0, "p_top": 0.7},
        {"server_id": "s2", "label_index": 1, "p_top": 0.6},
        {"server_id": "s2", "label_index": 2, "p_top": 0.6},
        {"server_id": "s2", "label_index": 3, "p_top": 0.6},
        {"server_id": "s2", "label_index": 4, "p_top": 0.6},
        {"server_id": "s2", "label_index": 5, "p_top": 0.6},
        {"server_id": "s2", "label_index": 6, "p_top": 0.6},
        {"server_id": "s3", "label_index": 0, "p_top": 0.3},
        {"server_id": "s3", "label_index": 1, "p_top": 0.2},
        {"server_id": "s3", "label_index": 2, "p_top": 0.2},
        {"server_id": "s3", "label_index": 3, "p_top": 0.2},
        {"server_id": "s3", "label_index": 4, "p_top": 0.2},
        {"server_id": "s3", "label_index": 5, "p_top": 0.2},
        {"server_id": "s3", "label_index": 6, "p_top": 0.2}
    ]

    for server in test_servers:
        test_db.add(McpServerRegistry(**server))
    for score in test_scores:
        test_db.add(McpLlmAxisScore(**score))
    test_db.commit()

    # Run test
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.post("/api/scoring/derive")
    assert response.status_code == 200
    assert response.json() == {"derived": 3, "updated": 3, "skipped": 0}

    # Verify results
    s1 = test_db.query(McpServerRegistry).filter(McpServerRegistry.server_id == "s1").first()
    s2 = test_db.query(McpServerRegistry).filter(McpServerRegistry.server_id == "s2").first()
    s3 = test_db.query(McpServerRegistry).filter(McpServerRegistry.server_id == "s3").first()
    assert s1.risk_tier == "TRUSTED_GENERAL"
    assert s2.risk_tier == "TRUSTED_RESEARCH"
    assert s3.risk_tier == "HIGH_RISK_ISOLATED"

    print("PASS")