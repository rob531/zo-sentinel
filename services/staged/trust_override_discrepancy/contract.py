from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from services.staged.trust_gating_override.contract import trust_gate

router = APIRouter(prefix="/api")

class Discrepancy(BaseModel):
    server_id: int
    name: str
    computed_tier: str
    override_trusted: bool
    url: str
    last_scored: Optional[str]

class DiscrepancyResponse(BaseModel):
    discrepancies: List[Discrepancy]

def get_risk_tier(score: float) -> str:
    if score >= 0.8:
        return "HIGH_RISK"
    elif score >= 0.5:
        return "MEDIUM_RISK"
    else:
        return "TRUSTED"

def compute_composite_score(scores: List[float]) -> float:
    return max(scores) if scores else 0.0

def get_discrepancies(db: Session) -> List[Discrepancy]:
    subquery = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.score).label("max_score")
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    query = (
        select(
            McpServerRegistry.id,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.last_scored,
            subquery.c.max_score
        )
        .join(subquery, McpServerRegistry.id == subquery.c.server_id)
    )

    results = db.execute(query).fetchall()

    discrepancies = []
    for row in results:
        server_id, name, url, last_scored, max_score = row
        computed_tier = get_risk_tier(max_score)
        override_trusted = trust_gate(url, name, {})
        override_tier = "TRUSTED" if override_trusted else "HIGH_RISK"

        if computed_tier != override_tier:
            discrepancies.append({
                "server_id": server_id,
                "name": name,
                "computed_tier": computed_tier,
                "override_trusted": override_trusted,
                "url": url,
                "last_scored": last_scored
            })

    return discrepancies

@router.get("/verdict/override-discrepancy", response_model=DiscrepancyResponse)
async def get_override_discrepancies(db: Session = Depends(get_session)):
    discrepancies = get_discrepancies(db)
    return {"discrepancies": discrepancies}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock trust_gate function
    def mock_trust_gate(url: str, name: str, metadata: dict) -> bool:
        return name in ["trusted1", "trusted2"]

    app.dependency_overrides[trust_gate] = mock_trust_gate

    # Seed test data
    with SessionLocal() as db:
        servers = [
            {"id": 1, "name": "high_risk1", "url": "http://highrisk1.com", "last_scored": "2023-01-01"},
            {"id": 2, "name": "high_risk2", "url": "http://highrisk2.com", "last_scored": "2023-01-02"},
            {"id": 3, "name": "trusted1", "url": "http://trusted1.com", "last_scored": "2023-01-03"},
            {"id": 4, "name": "trusted2", "url": "http://trusted2.com", "last_scored": "2023-01-04"},
            {"id": 5, "name": "trusted3", "url": "http://trusted3.com", "last_scored": "2023-01-05"},
        ]
        db.bulk_insert_mappings(McpServerRegistry, servers)

        scores = [
            {"server_id": 1, "axis": "axis1", "score": 0.9},
            {"server_id": 2, "axis": "axis1", "score": 0.9},
            {"server_id": 3, "axis": "axis1", "score": 0.4},
            {"server_id": 4, "axis": "axis1", "score": 0.4},
            {"server_id": 5, "axis": "axis1", "score": 0.4},
        ]
        db.bulk_insert_mappings(McpLlmAxisScore, scores)
        db.commit()

    client = TestClient(app)
    response = client.get("/api/verdict/override-discrepancy")
    assert response.status_code == 200
    data = response.json()
    assert len(data["discrepancies"]) == 3  # 2 high_risk + 1 trusted3 (override False)
    print("PASS")