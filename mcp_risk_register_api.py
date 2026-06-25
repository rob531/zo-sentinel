from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import McpRiskRegister

router = APIRouter()

class RiskRegisterEntry(BaseModel):
    server_id: str
    risk_tier: str
    computed_at: datetime
    expires_at: datetime
    reason: str

@router.get("/api/risk_register", response_model=List[RiskRegisterEntry])
async def get_risk_register(
    db: Session = Depends(get_db),
    risk_tier: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None)
):
    query = select(McpRiskRegister)

    if risk_tier:
        query = query.where(McpRiskRegister.risk_tier == risk_tier)

    if sort_by == "computed_at":
        query = query.order_by(desc(McpRiskRegister.computed_at))

    results = db.execute(query).scalars().all()

    return [
        {
            "server_id": entry.server_id,
            "risk_tier": entry.risk_tier,
            "computed_at": entry.computed_at,
            "expires_at": entry.expires_at,
            "reason": entry.reason
        }
        for entry in results
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # Test basic endpoint
    response = client.get("/api/risk_register")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    print("PASS: Basic endpoint returns list")

    # Test filtering by risk_tier
    response = client.get("/api/risk_register?risk_tier=high")
    assert response.status_code == 200
    assert all(entry["risk_tier"] == "high" for entry in response.json())
    print("PASS: Filtering by risk_tier works")

    # Test sorting by computed_at
    response = client.get("/api/risk_register?sort_by=computed_at")
    assert response.status_code == 200
    computed_at_values = [entry["computed_at"] for entry in response.json()]
    assert computed_at_values == sorted(computed_at_values, reverse=True)
    print("PASS: Sorting by computed_at works")