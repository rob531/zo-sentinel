from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

class AxisDetailResponse(BaseModel):
    axis: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: str

@router.get("/servers/{server_id}/axes/{axis_name}", response_model=AxisDetailResponse)
async def get_axis_detail(server_id: str, axis_name: str, db: Session = Depends(get_session)) -> dict:
    axis = db.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id,
        MCPLLMAxisScore.axis_name == axis_name
    ).first()

    if not axis:
        raise HTTPException(status_code=404, detail="Axis not found")

    return {
        "axis": axis.axis_name,
        "label": axis.label,
        "p_top": axis.p_top,
        "p_critical": axis.p_critical,
        "p_danger": axis.p_danger,
        "scored_at": axis.scored_at.isoformat()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScore
    from app.dependency_overrides import dependency_overrides
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    dependency_overrides[get_session] = lambda: TestSession()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Insert test data
    test_session = TestSession()
    test_axis = MCPLLMAxisScore(
        server_id="test_server",
        axis_name="overall_risk",
        label="Overall Risk",
        p_top=0.9,
        p_critical=0.7,
        p_danger=0.5,
        scored_at=datetime.now()
    )
    test_session.add(test_axis)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test_server/axes/overall_risk")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"axis", "label", "p_top", "p_critical", "p_danger", "scored_at"}
    assert isinstance(data["p_top"], float)
    assert isinstance(data["p_critical"], float)
    assert isinstance(data["p_danger"], float)
    assert isinstance(data["scored_at"], str)
    print("PASS")