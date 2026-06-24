import os
import pytest
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# --- Database Setup ---
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Server(Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String, unique=True, index=True)

class LLMAxisScore(Base):
    __tablename__ = "mcp_llm_axis_scores"
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    axis_name = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    evidence = Column(Text, nullable=False)

    server = relationship("Server")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic Models ---
class LLMAxisScoreResponse(BaseModel):
    axis_name: str
    score: int
    evidence: str

class VerdictViewResponse(BaseModel):
    server_id: int
    hostname: str
    overall_risk: LLMAxisScoreResponse
    risk_axes: List[LLMAxisScoreResponse]

# --- FastAPI Router ---
router = APIRouter()

@router.get("/servers/{server_id}/verdict-view", response_model=VerdictViewResponse)
def get_verdict_view(server_id: int, db: Session = Depends(get_db)):
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = db.query(LLMAxisScore).filter(LLMAxisScore.server_id == server_id).all()
    if not scores:
        raise HTTPException(status_code=404, detail="Verdict scores not found for this server")

    risk_axes_data = []
    overall_risk_score = None

    for score_item in scores:
        response_item = LLMAxisScoreResponse(
            axis_name=score_item.axis_name,
            score=score_item.score,
            evidence=score_item.evidence
        )
        if score_item.axis_name == "overall_risk":
            overall_risk_score = response_item
        else:
            risk_axes_data.append(response_item)

    if not overall_risk_score:
        raise HTTPException(status_code=404, detail="Overall risk score not found")

    return VerdictViewResponse(
        server_id=server.id,
        hostname=server.hostname,
        overall_risk=overall_risk_score,
        risk_axes=risk_axes_data
    )

# --- Self-Test ---
from fastapi.testclient import TestClient

app = FastAPI()
app.include_router(router)

@pytest.fixture(scope="module")
def test_client():
    # Use a separate in-memory DB for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    # Seed the in-memory database
    db = next(override_get_db())
    server1 = Server(id=1, hostname="test-server-1")
    db.add(server1)
    db.commit()
    db.refresh(server1)

    risk_axes_data = [
        {"axis_name": "network_exposure", "score": 8, "evidence": "Open SSH port detected."},
        {"axis_name": "vulnerability_management", "score": 5, "evidence": "Some outdated packages found."},
        {"axis_name": "access_control", "score": 7, "evidence": "Weak password policies."},
        {"axis_name": "data_sensitivity", "score": 3, "evidence": "No sensitive data identified."},
        {"axis_name": "compliance", "score": 6, "evidence": "Minor configuration deviations."},
        {"axis_name": "threat_intelligence", "score": 4, "evidence": "No active threats linked."},
        {"axis_name": "overall_risk", "score": 6, "evidence": "Moderate risk level."}
    ]

    for data in risk_axes_data:
        llm_score = LLMAxisScore(
            server_id=server1.id,
            axis_name=data["axis_name"],
            score=data["score"],
            evidence=data["evidence"]
        )
        db.add(llm_score)
    db.commit()

    yield client
    Base.metadata.drop_all(bind=test_engine)


def test_get_verdict_view(test_client: TestClient):
    response = test_client.get("/servers/1/verdict-view")
    assert response.status_code == 200
    data = response.json()

    assert data["server_id"] == 1
    assert data["hostname"] == "test-server-1"
    assert data["overall_risk"]["axis_name"] == "overall_risk"
    assert data["overall_risk"]["score"] == 6
    assert data["overall_risk"]["evidence"] == "Moderate risk level."

    assert len(data["risk_axes"]) == 6
    # Check if all risk axes are present and have correct data
    expected_axes = {
        "network_exposure", "vulnerability_management", "access_control",
        "data_sensitivity", "compliance", "threat_intelligence"
    }
    returned_axes = {axis["axis_name"] for axis in data["risk_axes"]}
    assert returned_axes == expected_axes

    # Check specific axis details
    for axis in data["risk_axes"]:
        if axis["axis_name"] == "network_exposure":
            assert axis["score"] == 8
            assert axis["evidence"] == "Open SSH port detected."
        elif axis["axis_name"] == "vulnerability_management":
            assert axis["score"] == 5
            assert axis["evidence"] == "Some outdated packages found."
        # Add checks for other axes if needed

    print("PASS")

if __name__ == "__main__":
    # This block is for running the FastAPI app directly, not for the test
    # To run the test, use `pytest your_file_name.py`
    import uvicorn
    print("Running FastAPI application. To run tests, use 'pytest'.")
    uvicorn.run(app, host="0.0.0.0", port=8000)