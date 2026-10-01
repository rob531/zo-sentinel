# deps: fastapi, pydantic, sqlalchemy, sqlmodel, PyJWT, passlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org, User, ApiKey

router = APIRouter()

class RiskTierComparisonRequest(BaseModel):
    server_ids: List[str]
    org_id: str

class RiskTierComparisonResponse(BaseModel):
    server_id: str
    name: str
    current_risk_tier: str
    previous_risk_tier: str
    change_reason: str

@router.post("/compare", response_model=List[RiskTierComparisonResponse])
async def compare_risk_tiers(
    request: RiskTierComparisonRequest,
    db: Session = Depends(get_session)
):
    # Get the organization to ensure it exists and the user has access
    org = db.query(Org).filter(Org.id == request.org_id).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found"
        )

    # Get the servers with their current risk tiers
    servers = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id.in_(request.server_ids),
        McpServerRegistry.org_id == request.org_id
    ).all()

    if not servers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No servers found"
        )

    # Get the previous risk tiers for comparison
    previous_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id.in_(request.server_ids),
        McpLlmAxisScore.org_id == request.org_id
    ).order_by(McpLlmAxisScore.scored_at.desc()).all()

    # Prepare the response
    response = []
    for server in servers:
        current_tier = server.risk_tier
        previous_tier = "N/A"
        change_reason = "No previous score"

        for score in previous_scores:
            if score.server_id == server.server_id:
                previous_tier = score.label
                change_reason = f"Changed from {previous_tier} to {current_tier}"
                break

        response.append({
            "server_id": server.server_id,
            "name": server.name,
            "current_risk_tier": current_tier,
            "previous_risk_tier": previous_tier,
            "change_reason": change_reason
        })

    return response

if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the get_session dependency for testing
    def override_get_session():
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Create tables and seed test data
    Base.metadata.create_all(bind=engine)
    db = next(override_get_session())
    test_org = Org(id="test_org", name="Test Org")
    db.add(test_org)
    db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.post(
        "/compare",
        json={"server_ids": ["server1", "server2"], "org_id": "test_org"}
    )

    if response.status_code == 200 and len(response.json()) == 2:
        print("PASS")
    else:
        print(f"FAIL: {response.json()}")