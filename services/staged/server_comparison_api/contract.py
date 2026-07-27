from typing import List, Dict, Optional
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float

class ServerData(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    axes: Dict[str, AxisScore]

class ComparisonResult(BaseModel):
    axis: str
    left_p_top: float
    right_p_top: float
    delta: float
    winner: str

class ServerComparisonResponse(BaseModel):
    servers: List[ServerData]
    comparison: List[ComparisonResult]

def get_server_comparison(ids: str, session: Session = Depends(get_session)) -> ServerComparisonResponse:
    server_ids = ids.split(',')
    if len(server_ids) != 2:
        raise HTTPException(status_code=400, detail="Exactly two server IDs must be provided")

    servers = session.query(McpServerRegistry).filter(McpServerRegistry.server_id.in_(server_ids)).all()
    if len(servers) != 2:
        raise HTTPException(status_code=404, detail="One or both servers not found")

    server_data = []
    for server in servers:
        axes = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server.server_id).all()
        axis_dict = {
            axis.axis: {
                "label": axis.label,
                "p_top": axis.p_top,
                "p_critical": axis.p_critical
            }
            for axis in axes
        }
        server_data.append({
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "axes": axis_dict
        })

    comparison = []
    left_server = server_data[0]
    right_server = server_data[1]

    for axis in left_server["axes"]:
        if axis in right_server["axes"]:
            left_p_top = left_server["axes"][axis]["p_top"]
            right_p_top = right_server["axes"][axis]["p_top"]
            delta = right_p_top - left_p_top
            winner = "left" if delta < 0 else "right" if delta > 0 else "tie"
            comparison.append({
                "axis": axis,
                "left_p_top": left_p_top,
                "right_p_top": right_p_top,
                "delta": delta,
                "winner": winner
            })

    return ServerComparisonResponse(
        servers=server_data,
        comparison=comparison
    )

@app.get("/api/servers/compare", response_model=ServerComparisonResponse)
def compare_servers(ids: str, response: ServerComparisonResponse = Depends(get_server_comparison)):
    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(server_id="server1", name="Test Server 1", risk_tier="low"),
        McpServerRegistry(server_id="server2", name="Test Server 2", risk_tier="medium"),
        McpLlmAxisScore(server_id="server1", axis="axis1", label="Test Axis 1", p_top=0.8, p_critical=0.2),
        McpLlmAxisScore(server_id="server1", axis="axis2", label="Test Axis 2", p_top=0.6, p_critical=0.3),
        McpLlmAxisScore(server_id="server2", axis="axis1", label="Test Axis 1", p_top=0.7, p_critical=0.25),
        McpLlmAxisScore(server_id="server2", axis="axis2", label="Test Axis 2", p_top=0.5, p_critical=0.4),
    ])
    test_session.commit()

    # Run test
    client = TestClient(app)
    response = client.get("/api/servers/compare?ids=server1,server2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["comparison"]) == 2
    assert any(item["delta"] != 0 for item in data["comparison"])

    print("PASS")