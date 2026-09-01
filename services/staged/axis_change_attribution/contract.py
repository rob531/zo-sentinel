from sqlalchemy.pool import StaticPool
from typing import List, Dict, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/axis")

class AxisChange(BaseModel):
    date: datetime
    old_label: str
    new_label: str

class AxisData(BaseModel):
    name: str
    changes: List[AxisChange]

class AxisResponse(BaseModel):
    axes: List[AxisData]

def get_axis_changes(server_id: int, session: Session = Depends(get_session)) -> AxisResponse:
    # Query the database for axis changes for the given server_id
    scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    # Group scores by axis_name
    axis_dict = {}
    for score in scores:
        if score.axis_name not in axis_dict:
            axis_dict[score.axis_name] = []
        axis_dict[score.axis_name].append(score)

    # Prepare the response data
    axes = []
    for axis_name, scores in axis_dict.items():
        changes = []
        for i in range(1, len(scores)):
            changes.append({
                "date": scores[i].created_at,
                "old_label": scores[i-1].label,
                "new_label": scores[i].label
            })
        axes.append({
            "name": axis_name,
            "changes": changes
        })

    return {"axes": axes}

@router.get("/{server_id}/changes", response_model=AxisResponse)
async def axis_changes(server_id: int, session: Session = Depends(get_session)):
    try:
        return get_axis_changes(server_id, session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    StaticPool.bind = engine
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Seed test data
    session = SessionLocal()
    server = McpServerRegistry(server_id=1, hostname="test.example.com")
    session.add(server)

    # Add initial axis scores
    session.add(McpLlmAxisScore(
        server_id=1,
        axis_name="security",
        label="low",
        created_at=datetime(2023, 1, 1)
    ))
    session.add(McpLlmAxisScore(
        server_id=1,
        axis_name="performance",
        label="medium",
        created_at=datetime(2023, 1, 1)
    ))

    # Add changed axis scores
    session.add(McpLlmAxisScore(
        server_id=1,
        axis_name="security",
        label="medium",
        created_at=datetime(2023, 1, 2)
    ))
    session.add(McpLlmAxisScore(
        server_id=1,
        axis_name="security",
        label="high",
        created_at=datetime(2023, 1, 3)
    ))
    session.add(McpLlmAxisScore(
        server_id=1,
        axis_name="performance",
        label="high",
        created_at=datetime(2023, 1, 2)
    ))

    session.commit()

    # Test the endpoint
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/axis/1/changes")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert len(data["axes"]) == 2
    security_axis = next(axis for axis in data["axes"] if axis["name"] == "security")
    assert len(security_axis["changes"]) == 2
    performance_axis = next(axis for axis in data["axes"] if axis["name"] == "performance")
    assert len(performance_axis["changes"]) == 1

    print("PASS")