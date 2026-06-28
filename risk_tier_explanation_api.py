from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

Base = declarative_base()

class MCPLLMAxisScore(Base):
    __tablename__ = 'mcp_llm_axis_scores'
    id = Column(Integer, primary_key=True)
    server_id = Column(String, ForeignKey('servers.id'))
    axis = Column(String)
    score = Column(Float)
    confidence = Column(Float)

class MCPSignalScore(Base):
    __tablename__ = 'mcp_signal_scores'
    id = Column(Integer, primary_key=True)
    server_id = Column(String, ForeignKey('servers.id'))
    signal = Column(String)
    score = Column(Float)
    confidence = Column(Float)
    threshold = Column(Float)

class Server(Base):
    __tablename__ = 'servers'
    id = Column(String, primary_key=True)
    risk_tier = Column(String)

class RiskExplanation(BaseModel):
    server_id: str
    risk_tier: str
    explanation: str

router = APIRouter()

def get_db():
    engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/servers/{server_id}/risk_explanation", response_model=RiskExplanation)
async def get_risk_explanation(server_id: str, db: Session = Depends(get_db)):
    # Get server's risk tier
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get LLM axis scores
    llm_scores = db.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()

    # Get signal scores
    signal_scores = db.query(MCPSignalScore).filter(MCPSignalScore.server_id == server_id).all()

    # Generate explanation
    explanation_parts = []

    # Add LLM axis scores to explanation
    if llm_scores:
        explanation_parts.append("LLM Axis Scores:")
        for score in llm_scores:
            explanation_parts.append(
                f"- {score.axis}: score={score.score:.2f}, confidence={score.confidence:.2f}"
            )

    # Add signal scores to explanation
    if signal_scores:
        explanation_parts.append("Signal Scores:")
        for score in signal_scores:
            explanation_parts.append(
                f"- {score.signal}: score={score.score:.2f}, confidence={score.confidence:.2f}, threshold={score.threshold:.2f}"
            )

    # Add risk tier explanation
    explanation_parts.append(f"Risk Tier: {server.risk_tier} was assigned based on the above scores.")

    explanation = "\n".join(explanation_parts)

    return {
        "server_id": server_id,
        "risk_tier": server.risk_tier,
        "explanation": explanation
    }

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # Seed test data
    def seed_test_data(db: Session):
        # Create test server
        test_server = Server(id="test_server", risk_tier="High")
        db.add(test_server)

        # Add LLM axis scores
        llm_scores = [
            MCPLLMAxisScore(server_id="test_server", axis="Security", score=0.9, confidence=0.95),
            MCPLLMAxisScore(server_id="test_server", axis="Performance", score=0.7, confidence=0.85)
        ]
        db.add_all(llm_scores)

        # Add signal scores
        signal_scores = [
            MCPSignalScore(server_id="test_server", signal="Malware", score=0.85, confidence=0.9, threshold=0.8),
            MCPSignalScore(server_id="test_server", signal="Phishing", score=0.75, confidence=0.85, threshold=0.7)
        ]
        db.add_all(signal_scores)
        db.commit()

    # Test the endpoint
    with TestClient(app) as client:
        # Seed the database
        with get_db() as db:
            seed_test_data(db)

        # Test the endpoint
        response = client.get("/servers/test_server/risk_explanation")
        assert response.status_code == 200
        assert response.json()["server_id"] == "test_server"
        assert response.json()["risk_tier"] == "High"
        assert "LLM Axis Scores" in response.json()["explanation"]
        assert "Signal Scores" in response.json()["explanation"]
        assert "Risk Tier: High was assigned based on the above scores." in response.json()["explanation"]

    print("PASS")