from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import requests
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores

router = APIRouter()

class AxisBreakdownResponse(BaseModel):
    server_id: str
    axes: Dict[str, Dict[str, object]]

def get_axis_breakdown(server_id: str) -> Dict[str, object]:
    session = Depends(get_session)
    try:
        scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
        if not scores:
            raise HTTPException(status_code=404, detail="Server not found")

        axes = {}
        for score in scores:
            axes[score.axis_name] = {
                "label": score.label,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger,
                "probs": score.probs
            }

        return {"server_id": server_id, "axes": axes}
    finally:
        session.close()

router.get("/servers/{server_id}/axis-breakdown")(get_axis_breakdown)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import MCPLLMAxisScores
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    MCPLLMAxisScores.__table__.create(engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    test_data = MCPLLMAxisScores(
        server_id="test_server",
        axis_name="test_axis",
        label="test_label",
        p_top=0.9,
        p_critical=0.8,
        p_danger=0.7,
        probs={"a": 0.1, "b": 0.2}
    )

    with SessionLocal() as session:
        session.add(test_data)
        session.commit()

    response = client.get("/servers/test_server/axis-breakdown")
    assert response.status_code == 200
    assert response.json()["server_id"] == "test_server"
    assert "axes" in response.json()
    assert "test_axis" in response.json()["axes"]
    assert response.json()["axes"]["test_axis"]["label"] == "test_label"
    assert response.json()["axes"]["test_axis"]["p_top"] == 0.9
    assert response.json()["axes"]["test_axis"]["p_critical"] == 0.8
    assert response.json()["axes"]["test_axis"]["p_danger"] == 0.7
    assert response.json()["axes"]["test_axis"]["probs"] == {"a": 0.1, "b": 0.2}

    print("PASS")