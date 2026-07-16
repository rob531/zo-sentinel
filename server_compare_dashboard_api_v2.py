from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from fastapi.testclient import TestClient
import uvicorn

router = APIRouter()

class AxisScore(BaseModel):
    p_top: float
    label: str

class ServerComparison(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    overall_risk_p_top: float
    axes: Dict[str, AxisScore]

class ComparisonResult(BaseModel):
    servers: List[ServerComparison]
    comparison: Dict[str, float]

def get_server_comparison(server_ids: List[str], session: Session) -> ComparisonResult:
    servers_data = []
    max_risk_delta = 0.0
    worst_axis = ""

    for server_id in server_ids:
        server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
        if not server:
            continue

        axes_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
        axes_dict = {}

        for axis in axes_scores:
            axes_dict[axis.axis_name] = {
                "p_top": axis.p_top,
                "label": axis.label
            }

        server_data = {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "overall_risk_p_top": server.overall_risk_p_top,
            "axes": axes_dict
        }
        servers_data.append(server_data)

    if len(servers_data) < 2:
        raise HTTPException(status_code=400, detail="At least 2 servers are required for comparison")

    for i in range(len(servers_data)):
        for j in range(i + 1, len(servers_data)):
            delta = abs(servers_data[i]["overall_risk_p_top"] - servers_data[j]["overall_risk_p_top"])
            if delta > max_risk_delta:
                max_risk_delta = delta

    for axis in ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
        min_score = min(server["axes"][axis]["p_top"] for server in servers_data)
        if min_score < float(worst_axis) or worst_axis == "":
            worst_axis = axis

    return {
        "servers": servers_data,
        "comparison": {
            "max_risk_delta": max_risk_delta,
            "worst_axis": worst_axis
        }
    }

@router.get("/servers/compare", response_model=ComparisonResult)
async def compare_servers(server_ids: str, session: Session = Depends(get_session)):
    server_ids_list = server_ids.split(',')
    if len(server_ids_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum of 10 servers can be compared")

    return get_server_comparison(server_ids_list, session)

if __name__ == "__main__":
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    test_app = FastAPI()
    test_app.include_router(router)

    test_client = TestClient(test_app)

    test_server1 = MCPServerRegistry(
        server_id="server1",
        name="Test Server 1",
        risk_tier="High",
        overall_risk_p_top=0.8
    )
    test_server2 = MCPServerRegistry(
        server_id="server2",
        name="Test Server 2",
        risk_tier="Medium",
        overall_risk_p_top=0.5
    )
    test_session.add(test_server1)
    test_session.add(test_server2)
    test_session.commit()

    test_axis1 = MCPLLMAxisScores(
        server_id="server1",
        axis_name="auth_strength",
        p_top=0.7,
        label="Strong"
    )
    test_axis2 = MCPLLMAxisScores(
        server_id="server1",
        axis_name="capability_breadth",
        p_top=0.6,
        label="Moderate"
    )
    test_axis3 = MCPLLMAxisScores(
        server_id="server2",
        axis_name="auth_strength",
        p_top=0.4,
        label="Weak"
    )
    test_axis4 = MCPLLMAxisScores(
        server_id="server2",
        axis_name="capability_breadth",
        p_top=0.3,
        label="Low"
    )
    test_session.add(test_axis1)
    test_session.add(test_axis2)
    test_session.add(test_axis3)
    test_session.add(test_axis4)
    test_session.commit()

    response = test_client.get("/servers/compare?server_ids=server1,server2")
    assert response.status_code == 200
    result = response.json()
    assert len(result["servers"]) >= 2
    assert "axes" in result["servers"][0]
    print("PASS")