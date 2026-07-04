from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from typing import Dict, Optional

router = APIRouter()

class RiskTierComparisonResponse(BaseModel):
    mcp1: str
    mcp2: str
    comparison: str

def get_risk_tier(mcp_id: str, db: Session) -> Optional[str]:
    mcp = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == mcp_id).first()
    if not mcp:
        return None
    return mcp.risk_tier

@router.get("/mcp/risk-tier-comparison", response_model=RiskTierComparisonResponse)
async def compare_risk_tiers(
    mcp1_id: str,
    mcp2_id: str,
    db: Session = Depends(get_session)
) -> Dict[str, str]:
    tier1 = get_risk_tier(mcp1_id, db)
    tier2 = get_risk_tier(mcp2_id, db)

    if tier1 is None or tier2 is None:
        raise HTTPException(status_code=404, detail="One or both MCPs not found")

    if tier1 == tier2:
        comparison = "equal"
    elif tier1 > tier2:
        comparison = f"{mcp1_id} is higher risk"
    else:
        comparison = f"{mcp2_id} is higher risk"

    return {
        "mcp1": tier1,
        "mcp2": tier2,
        "comparison": comparison
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    Base.metadata.create_all(engine)
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(id="mcp1", risk_tier="high"),
        MCPServerRegistry(id="mcp2", risk_tier="medium")
    ])
    test_session.commit()

    # Test
    client = TestClient(app)
    response = client.get("/mcp/risk-tier-comparison?mcp1_id=mcp1&mcp2_id=mcp2")
    assert response.status_code == 200
    assert response.json() == {
        "mcp1": "high",
        "mcp2": "medium",
        "comparison": "mcp1 is higher risk"
    }

    print("PASS")