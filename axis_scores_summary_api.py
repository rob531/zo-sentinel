from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional
from app.db import get_session
from app.models import MCPLLMAxisScores
from sqlalchemy.orm import Session
import requests

router = APIRouter()

def get_axis_scores(server_id: str, model_version: Optional[str] = None) -> Dict:
    session = Depends(get_session)

    query = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id)
    if model_version:
        query = query.filter(MCPLLMAxisScores.model_version == model_version)

    scores = query.all()

    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = {
        "overall_risk": {"label": "Overall Risk", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "auth_strength": {"label": "Auth Strength", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "capability_breadth": {"label": "Capability Breadth", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "data_sensitivity": {"label": "Data Sensitivity", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "network_egress": {"label": "Network Egress", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "maintainer_trust": {"label": "Maintainer Trust", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "exploit_surface": {"label": "Exploit Surface", "p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0}
    }

    for score in scores:
        if score.axis_name in axis_scores:
            axis_scores[score.axis_name]["p_top"] = score.p_top
            axis_scores[score.axis_name]["p_critical"] = score.p_critical
            axis_scores[score.axis_name]["p_danger"] = score.p_danger

    return axis_scores

@router.get("/servers/{server_id}/axis_scores")
async def read_axis_scores(server_id: str, model_version: Optional[str] = Query(None)):
    return get_axis_scores(server_id, model_version)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/servers/test-server/axis_scores")
    assert response.status_code == 200
    data = response.json()
    assert all(axis in data for axis in [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust",
        "exploit_surface"
    ])
    assert all(isinstance(data[axis]["p_top"], float) for axis in data)
    assert all(isinstance(data[axis]["p_critical"], float) for axis in data)
    assert all(isinstance(data[axis]["p_danger"], float) for axis in data)

    print("PASS")