from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from .logic import compare_servers

router = APIRouter(prefix="/api/servers")

class ServerComparisonResponse(BaseModel):
    servers: List[Dict]
    comparison: List[Dict]

@router.get("/compare", response_model=ServerComparisonResponse)
async def compare_server_axes(
    ids: str,
    session: Session = Depends(get_session)
):
    server_ids = [id.strip() for id in ids.split(",")]
    if len(server_ids) != 2:
        raise HTTPException(status_code=400, detail="Exactly two server IDs must be provided")

    result = compare_servers(session, server_ids[0], server_ids[1])
    return result

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    from app.db import SessionLocal
    app.dependency_overrides[get_session] = lambda: SessionLocal(test_engine)

    # Seed test data
    with SessionLocal(test_engine) as session:
        # Clear existing data
        session.query(McpLlmAxisScore).delete()
        session.query(McpServerRegistry).delete()

        # Add test servers
        server1 = McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="low",
            org_id=1
        )
        server2 = McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="medium",
            org_id=1
        )
        session.add_all([server1, server2])

        # Add test axis scores
        axis_scores = [
            McpLlmAxisScore(server_id="server1", axis="security", p_top=0.9, p_critical=0.1),
            McpLlmAxisScore(server_id="server1", axis="privacy", p_top=0.8, p_critical=0.2),
            McpLlmAxisScore(server_id="server2", axis="security", p_top=0.7, p_critical=0.3),
            McpLlmAxisScore(server_id="server2", axis="privacy", p_top=0.6, p_critical=0.4),
        ]
        session.add_all(axis_scores)
        session.commit()

    client = TestClient(app)
    response = client.get("/api/servers/compare?ids=server1,server2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["comparison"]) > 0
    assert any(item["delta"] != 0 for item in data["comparison"])

    print("PASS")