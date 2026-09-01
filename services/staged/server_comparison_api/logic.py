from typing import List, Dict, Optional
from pydantic import BaseModel
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class ServerAxisData(BaseModel):
    label: str
    p_top: float
    p_critical: float

class ServerData(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    axes: Dict[str, ServerAxisData]

class ComparisonAxis(BaseModel):
    axis: str
    left_p_top: float
    right_p_top: float
    delta: float
    winner: Optional[str]

class ComparisonResponse(BaseModel):
    servers: List[ServerData]
    comparison: List[ComparisonAxis]

def get_server_comparison(server_ids: List[str], session: Session = Depends(get_session)) -> ComparisonResponse:
    servers = session.query(McpServerRegistry).filter(McpServerRegistry.server_id.in_(server_ids)).all()
    if len(servers) != 2:
        raise ValueError("Exactly two servers must be provided for comparison")

    server_data = []
    for server in servers:
        axes = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server.server_id).all()
        axis_dict = {
            axis.axis: ServerAxisData(
                label=axis.label,
                p_top=axis.p_top,
                p_critical=axis.p_critical
            )
            for axis in axes
        }
        server_data.append(ServerData(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            axes=axis_dict
        ))

    comparison = []
    left_server = server_data[0]
    right_server = server_data[1]

    shared_axes = set(left_server.axes.keys()) & set(right_server.axes.keys())
    for axis in shared_axes:
        left_axis = left_server.axes[axis]
        right_axis = right_server.axes[axis]

        delta = left_axis.p_top - right_axis.p_top
        winner = None
        if delta > 0:
            winner = left_server.server_id
        elif delta < 0:
            winner = right_server.server_id

        comparison.append(ComparisonAxis(
            axis=axis,
            left_p_top=left_axis.p_top,
            right_p_top=right_axis.p_top,
            delta=delta,
            winner=winner
        ))

    return ComparisonResponse(servers=server_data, comparison=comparison)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: session

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    # Seed test data
    server1 = McpServerRegistry(
        server_id="server1",
        name="Server One",
        risk_tier="Tier 1"
    )
    server2 = McpServerRegistry(
        server_id="server2",
        name="Server Two",
        risk_tier="Tier 2"
    )
    session.add(server1)
    session.add(server2)

    axis1 = McpLlmAxisScore(
        server_id="server1",
        axis="axis1",
        label="Axis One",
        p_top=0.8,
        p_critical=0.2
    )
    axis2 = McpLlmAxisScore(
        server_id="server1",
        axis="axis2",
        label="Axis Two",
        p_top=0.6,
        p_critical=0.4
    )
    axis3 = McpLlmAxisScore(
        server_id="server2",
        axis="axis1",
        label="Axis One",
        p_top=0.7,
        p_critical=0.3
    )
    axis4 = McpLlmAxisScore(
        server_id="server2",
        axis="axis2",
        label="Axis Two",
        p_top=0.5,
        p_critical=0.5
    )
    session.add(axis1)
    session.add(axis2)
    session.add(axis3)
    session.add(axis4)
    session.commit()

    client = TestClient(app)
    response = client.get("/api/servers/compare?ids=server1,server2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["comparison"]) > 0
    assert any(abs(axis["delta"]) > 0 for axis in data["comparison"])

    print("PASS")