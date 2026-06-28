from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

Base = declarative_base()

class MCPLLMAxisScore(Base):
    __tablename__ = 'mcp_llm_axis_scores'
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer)
    overall_risk = Column(Float)
    risk_tier = Column(String)

class MCPPolicyRule(Base):
    __tablename__ = 'mcp_policy_rules'
    id = Column(Integer, primary_key=True)
    risk_tier = Column(String)
    max_allowed_risk = Column(Float)
    violation_message = Column(String)
    recommended_action = Column(String)

class ReadinessResponse(BaseModel):
    readiness_status: str
    policy_violations: List[str]
    recommended_action: str

router = APIRouter()

def get_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/servers/{server_id}/deployment_readiness", response_model=ReadinessResponse)
def get_deployment_readiness(server_id: int, db: Session = Depends(get_db)):
    # Get the risk score and tier for the server
    score = db.execute(
        text("SELECT overall_risk, risk_tier FROM mcp_llm_axis_scores WHERE server_id = :server_id"),
        {"server_id": server_id}
    ).fetchone()

    if not score:
        raise HTTPException(status_code=404, detail="Server not found")

    overall_risk, risk_tier = score

    # Get applicable policy rules
    rules = db.execute(
        text("SELECT max_allowed_risk, violation_message, recommended_action FROM mcp_policy_rules WHERE risk_tier = :risk_tier"),
        {"risk_tier": risk_tier}
    ).fetchall()

    policy_violations = []
    recommended_action = "Proceed with deployment"

    for rule in rules:
        max_allowed_risk, violation_message, action = rule
        if overall_risk > max_allowed_risk:
            policy_violations.append(violation_message)
            recommended_action = action

    readiness_status = "Ready" if not policy_violations else "Not Ready"

    return {
        "readiness_status": readiness_status,
        "policy_violations": policy_violations,
        "recommended_action": recommended_action
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed the database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    # Add test data
    db.add(MCPLLMAxisScore(server_id=1, overall_risk=0.3, risk_tier="low"))
    db.add(MCPLLMAxisScore(server_id=2, overall_risk=0.8, risk_tier="high"))
    db.add(MCPPolicyRule(risk_tier="low", max_allowed_risk=0.5, violation_message="Risk too high for low tier", recommended_action="Review and mitigate risks"))
    db.add(MCPPolicyRule(risk_tier="high", max_allowed_risk=0.7, violation_message="Risk too high for high tier", recommended_action="Do not deploy"))
    db.commit()

    client = TestClient(app)

    # Test server 1 (should be ready)
    response = client.get("/servers/1/deployment_readiness")
    assert response.status_code == 200
    assert response.json()["readiness_status"] == "Ready"
    assert response.json()["policy_violations"] == []
    assert response.json()["recommended_action"] == "Proceed with deployment"

    # Test server 2 (should not be ready)
    response = client.get("/servers/2/deployment_readiness")
    assert response.status_code == 200
    assert response.json()["readiness_status"] == "Not Ready"
    assert len(response.json()["policy_violations"]) == 1
    assert response.json()["recommended_action"] == "Do not deploy"

    print("PASS")