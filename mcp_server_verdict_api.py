from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import fastapi

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class VerdictResponse(BaseModel):
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str

def get_db() -> Session:
    # Mock database session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def compute_verdict(axes: Dict[str, AxisScore], overall: float) -> str:
    # Rule-override mechanism for risk tier
    critical_axes = {
        'injection_resilience': 0.3,
        'data_safety': 0.4,
        'access_control': 0.5
    }

    for axis, threshold in critical_axes.items():
        if axis in axes and axes[axis].p_top < threshold:
            return 'CRITICAL'

    if overall >= 0.8:
        return 'LOW'
    elif overall >= 0.6:
        return 'MEDIUM'
    elif overall >= 0.4:
        return 'HIGH'
    else:
        return 'CRITICAL'

@router.get("/servers/{server_id}/verdict", response_model=VerdictResponse)
async def get_verdict(server_id: str, db: Session = Depends(get_db)):
    # Query the database for the server's scores
    query = text("""
        SELECT axis, label, p_top, overall_risk, criteria_version
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
    """)
    result = db.execute(query, {"server_id": server_id}).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = {}
    overall = 0.0
    criteria_version = ""

    for row in result:
        axes[row.axis] = AxisScore(label=row.label, p_top=row.p_top)
        overall = row.overall_risk
        criteria_version = row.criteria_version

    risk_tier = compute_verdict(axes, overall)

    return {
        "axes": axes,
        "overall": overall,
        "risk_tier": risk_tier,
        "criteria_version": criteria_version
    }

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Column, String, Float, Integer
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker

    Base = declarative_base()

    class MCPLLMAxisScore(Base):
        __tablename__ = 'mcp_llm_axis_scores'
        id = Column(Integer, primary_key=True)
        server_id = Column(String)
        axis = Column(String)
        label = Column(String)
        p_top = Column(Float)
        overall_risk = Column(Float)
        criteria_version = Column(String)

    # Create in-memory database and seed with test data
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    db.add_all([
        MCPLLMAxisScore(
            server_id="test_server_1",
            axis="injection_resilience",
            label="Injection Resilience",
            p_top=0.2,
            overall_risk=0.7,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server_1",
            axis="data_safety",
            label="Data Safety",
            p_top=0.5,
            overall_risk=0.7,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server_2",
            axis="injection_resilience",
            label="Injection Resilience",
            p_top=0.4,
            overall_risk=0.9,
            criteria_version="v1.0"
        ),
        MCPLLMAxisScore(
            server_id="test_server_2",
            axis="data_safety",
            label="Data Safety",
            p_top=0.6,
            overall_risk=0.9,
            criteria_version="v1.0"
        )
    ])
    db.commit()

    app = fastapi.FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test critical axis override
    response = client.get("/servers/test_server_1/verdict")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "CRITICAL"

    # Test composite score tier
    response = client.get("/servers/test_server_2/verdict")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "LOW"

    print("PASS")