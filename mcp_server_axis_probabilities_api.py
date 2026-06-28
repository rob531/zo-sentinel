from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Dict, Any

from .database import get_db
from .models import MCPLLMAxisScores

router = APIRouter()

class AxisProbabilitiesResponse(BaseModel):
    axis_name: str
    label: str
    probs: Dict[str, Any]

@router.get("/servers/{server_id}/axes/{axis_name}/probabilities", response_model=AxisProbabilitiesResponse)
async def get_axis_probabilities(
    server_id: int,
    axis_name: str,
    db: Session = Depends(get_db)
):
    # Query the database for the specific server_id and axis_name
    stmt = select(MCPLLMAxisScores).where(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name == axis_name
    )
    result = db.execute(stmt).scalar_one_or_none()

    if not result:
        raise HTTPException(status_code=404, detail="Axis probabilities not found")

    return {
        "axis_name": result.axis_name,
        "label": result.label,
        "probs": result.probs
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from .database import Base, engine
    from .models import MCPLLMAxisScores

    # Create tables
    Base.metadata.create_all(engine)

    # Seed test data
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    test_data = MCPLLMAxisScores(
        server_id=1,
        axis_name="test_axis",
        label="Test Label",
        probs={"option1": 0.7, "option2": 0.3}
    )
    db.add(test_data)
    db.commit()

    # Create FastAPI app and add router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/1/axes/test_axis/probabilities")

    assert response.status_code == 200
    assert response.json() == {
        "axis_name": "test_axis",
        "label": "Test Label",
        "probs": {"option1": 0.7, "option2": 0.3}
    }

    print("PASS")