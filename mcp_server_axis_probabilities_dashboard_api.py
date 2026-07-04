from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, List
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPLLMAxisScores

router = APIRouter()

class ServerAxisProbabilities(BaseModel):
    server_id: int
    axis_probabilities: Dict[str, Dict[str, float]]

class DashboardResponse(BaseModel):
    top_servers_per_axis: Dict[str, List[ServerAxisProbabilities]]

def get_top_servers_per_axis(db: Session) -> Dict[str, List[ServerAxisProbabilities]]:
    # Query to get all axis scores grouped by server_id and axis
    results = db.query(
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.axis,
        MCPLLMAxisScores.label,
        MCPLLMAxisScores.probability
    ).all()

    # Group by axis and then by server_id to find top servers per axis
    axis_data = {}
    for server_id, axis, label, probability in results:
        if axis not in axis_data:
            axis_data[axis] = {}
        if server_id not in axis_data[axis]:
            axis_data[axis][server_id] = {}
        axis_data[axis][server_id][label] = probability

    # For each axis, get top 5 servers by highest probability
    top_servers_per_axis = {}
    for axis, servers in axis_data.items():
        # Sort servers by their highest probability label
        sorted_servers = sorted(
            servers.items(),
            key=lambda x: max(x[1].values()) if x[1] else 0,
            reverse=True
        )[:5]

        # Format the response
        top_servers_per_axis[axis] = [
            ServerAxisProbabilities(
                server_id=server_id,
                axis_probabilities=probabilities
            )
            for server_id, probabilities in sorted_servers
        ]

    return top_servers_per_axis

@router.get("/dashboard/server-axis-probabilities", response_model=DashboardResponse)
async def get_server_axis_probabilities(db: Session = Depends(get_session)):
    return {"top_servers_per_axis": get_top_servers_per_axis(db)}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScores, MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    test_data = [
        MCPLLMAxisScores(server_id=1, axis="axis1", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=1, axis="axis1", label="label2", probability=0.1),
        MCPLLMAxisScores(server_id=2, axis="axis1", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=2, axis="axis1", label="label2", probability=0.2),
        MCPLLMAxisScores(server_id=3, axis="axis1", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=3, axis="axis1", label="label2", probability=0.3),
        MCPLLMAxisScores(server_id=4, axis="axis1", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=4, axis="axis1", label="label2", probability=0.4),
        MCPLLMAxisScores(server_id=5, axis="axis1", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=5, axis="axis1", label="label2", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis1", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=6, axis="axis1", label="label2", probability=0.6),
        MCPLLMAxisScores(server_id=1, axis="axis2", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis2", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis2", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis2", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis2", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis2", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=1, axis="axis3", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis3", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis3", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis3", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis3", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis3", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=1, axis="axis4", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis4", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis4", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis4", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis4", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis4", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=1, axis="axis5", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis5", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis5", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis5", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis5", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis5", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=1, axis="axis6", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis6", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis6", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis6", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis6", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis6", label="label1", probability=0.4),
        MCPLLMAxisScores(server_id=1, axis="axis7", label="label1", probability=0.9),
        MCPLLMAxisScores(server_id=2, axis="axis7", label="label1", probability=0.8),
        MCPLLMAxisScores(server_id=3, axis="axis7", label="label1", probability=0.7),
        MCPLLMAxisScores(server_id=4, axis="axis7", label="label1", probability=0.6),
        MCPLLMAxisScores(server_id=5, axis="axis7", label="label1", probability=0.5),
        MCPLLMAxisScores(server_id=6, axis="axis7", label="label1", probability=0.4),
    ]

    db = SessionLocal()
    db.add_all(test_data)
    db.commit()

    # Create a test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/dashboard/server-axis-probabilities")
    assert response.status_code == 200
    data = response.json()

    # Verify the response contains all 7 axes and top 5 servers per axis
    assert len(data["top_servers_per_axis"]) == 7
    for axis, servers in data["top_servers_per_axis"].items():
        assert len(servers) == 5

    print("PASS")