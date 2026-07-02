from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores
from pydantic import BaseModel
from typing import List, Dict, Any

router = APIRouter()

class AxisProbability(BaseModel):
    axis_name: str
    probs: Dict[str, Any]

class ServerAxisProbabilities(BaseModel):
    server_id: int
    axes: List[AxisProbability]

class ServerAxisProbabilitiesResponse(BaseModel):
    servers: List[ServerAxisProbabilities]

@router.get("/server-axis-probabilities", response_model=ServerAxisProbabilitiesResponse)
def get_server_axis_probabilities(db: Session = Depends(get_session)):
    servers = db.query(MCPServerRegistry).all()
    result = []
    for server in servers:
        axes = []
        axis_scores = db.query(MCPAxisScores).filter(MCPAxisScores.server_id == server.id).all()
        for axis_score in axis_scores:
            axes.append(AxisProbability(axis_name=axis_score.axis_name, probs=axis_score.probs))
        result.append(ServerAxisProbabilities(server_id=server.id, axes=axes))
    return {"servers": result}

def seed_db(db: Session):
    server1 = MCPServerRegistry(id=1, name="Server1")
    server2 = MCPServerRegistry(id=2, name="Server2")
    db.add(server1)
    db.add(server2)
    db.commit()

    axis_score1 = MCPAxisScores(server_id=1, axis_name="Axis1", probs={"prob1": 0.5, "prob2": 0.5})
    axis_score2 = MCPAxisScores(server_id=1, axis_name="Axis2", probs={"prob1": 0.3, "prob2": 0.7})
    axis_score3 = MCPAxisScores(server_id=2, axis_name="Axis1", probs={"prob1": 0.2, "prob2": 0.8})
    db.add(axis_score1)
    db.add(axis_score2)
    db.add(axis_score3)
    db.commit()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            seed_db(db)
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(app)

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    response = client.get("/server-axis-probabilities")
    assert response.status_code == 200
    assert response.json() == {
        "servers": [
            {
                "server_id": 1,
                "axes": [
                    {"axis_name": "Axis1", "probs": {"prob1": 0.5, "prob2": 0.5}},
                    {"axis_name": "Axis2", "probs": {"prob1": 0.3, "prob2": 0.7}}
                ]
            },
            {
                "server_id": 2,
                "axes": [
                    {"axis_name": "Axis1", "probs": {"prob1": 0.2, "prob2": 0.8}}
                ]
            }
        ]
    }
    print("PASS")