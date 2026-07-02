from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class AxisHistoryResponse(BaseModel):
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str

@router.get("/servers/{server_id}/axis-history", response_model=AxisHistoryResponse)
def get_axis_history(server_id: str, session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).first()
    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = {
        "axis1": AxisScore(label=scores.axis1_label, p_top=scores.axis1_p_top),
        "axis2": AxisScore(label=scores.axis2_label, p_top=scores.axis2_p_top),
        "axis3": AxisScore(label=scores.axis3_label, p_top=scores.axis3_p_top),
        "axis4": AxisScore(label=scores.axis4_label, p_top=scores.axis4_p_top),
        "axis5": AxisScore(label=scores.axis5_label, p_top=scores.axis5_p_top),
        "axis6": AxisScore(label=scores.axis6_label, p_top=scores.axis6_p_top),
    }

    risk_tier = scores.risk_tier
    if any(axis.p_top >= 0.9 for axis in axes.values()):
        risk_tier = "CRITICAL"

    return {
        "axes": axes,
        "overall": scores.overall_risk,
        "risk_tier": risk_tier,
        "criteria_version": scores.criteria_version
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, McpLlmAxisScores

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)
    client.app.dependency_overrides[get_session] = override_get_session

    db = SessionLocal()
    test_server = McpLlmAxisScores(
        server_id="test_server",
        axis1_label="Axis 1",
        axis1_p_top=0.8,
        axis2_label="Axis 2",
        axis2_p_top=0.7,
        axis3_label="Axis 3",
        axis3_p_top=0.6,
        axis4_label="Axis 4",
        axis4_p_top=0.5,
        axis5_label="Axis 5",
        axis5_p_top=0.4,
        axis6_label="Axis 6",
        axis6_p_top=0.9,
        overall_risk=0.65,
        risk_tier="HIGH",
        criteria_version="1.0"
    )
    db.add(test_server)
    db.commit()
    db.close()

    response = client.get("/servers/test_server/axis-history")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 6
    assert data["overall"] == 0.65
    assert data["risk_tier"] == "CRITICAL"
    assert data["criteria_version"] == "1.0"

    print("PASS")