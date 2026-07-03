from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Any
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class AxisProbabilities(BaseModel):
    labels: Dict[str, float]

class AxisResponse(BaseModel):
    axis: Dict[str, AxisProbabilities]

@router.get("/probabilities/axis", response_model=AxisResponse)
def get_axis_probabilities(axis: str, session: Session = Depends(get_session)):
    axis_scores = session.query(McpLlmAxisScores).filter(McpLlmAxisScores.axis == axis).first()
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Axis not found")
    return {"axis": {axis: {"labels": axis_scores.probs}}}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)
    client.app.dependency_overrides[get_session] = override_get_session

    test_data = McpLlmAxisScores(axis="test_axis", probs={"label1": 0.3, "label2": 0.5, "label3": 0.2})
    db = next(override_get_session())
    db.add(test_data)
    db.commit()

    response = client.get("/probabilities/axis?axis=test_axis")
    assert response.status_code == 200
    assert response.json() == {"axis": {"test_axis": {"labels": {"label1": 0.3, "label2": 0.5, "label3": 0.2}}}}

    print("PASS")