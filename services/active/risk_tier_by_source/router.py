from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict
from app.db import get_session
from app.models import MCPServerRegistry, Organization
import requests
from pydantic import BaseModel

router = APIRouter(prefix="/api/risk_tier_by_source", tags=["risk_tier_by_source"])

class RiskTierResponse(BaseModel):
    source: str
    risk_tier: int
    evidence: Dict

@router.get("/{source_id}", response_model=RiskTierResponse)
async def get_risk_tier_by_source(
    source_id: str,
    session=Depends(get_session)
):
    # First check APP tables for basic info
    server = session.query(MCPServerRegistry).filter_by(server_id=source_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Source not found")

    org = session.query(Organization).filter_by(id=server.org_id).first()

    # Then check MESH tables via write_service
    try:
        mesh_response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "sql": \"\"
                SELECT risk_tier, evidence
                FROM mcp_signal_scores
                WHERE source_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                \"\",
                "params": [source_id]
            },
            timeout=5
        )
        mesh_response.raise_for_status()
        mesh_data = mesh_response.json()

        if mesh_data and mesh_data[0]:
            return {
                "source": source_id,
                "risk_tier": mesh_data[0]["risk_tier"],
                "evidence": mesh_data[0]["evidence"]
            }
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error accessing MESH data: {str(e)}")

    # Fallback calculation if no MESH data
    base_tier = 50  # neutral starting point
    if org and org.reputation_score > 80:
        base_tier -= 10
    elif org and org.reputation_score < 30:
        base_tier += 15

    return {
        "source": source_id,
        "risk_tier": max(0, min(100, base_tier)),
        "evidence": {
            "fallback": True,
            "org_reputation": org.reputation_score if org else None,
            "server_status": server.status
        }
    }

if __name__ == "__main__":
    import uvicorn
    from app.dependency_overrides import dependency_overrides

    # Test configuration
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Override data layer for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    from app.models import Base
    Base.metadata.create_all(test_engine)

    test_session = TestSession()
    test_org = Organization(id=1, name="Test Org", reputation_score=75)
    test_server = MCPServerRegistry(
        server_id="test-source-1",
        org_id=1,
        status="active",
        last_seen="2023-01-01T00:00:00Z"
    )
    test_session.add_all([test_org, test_server])
    test_session.commit()

    # Run test server
    uvicorn.run(app, host="127.0.0.1", port=8000)