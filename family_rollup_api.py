from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Dict
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func, case

router = APIRouter()

class FamilyRollupItem(BaseModel):
    family: str
    server_count: int
    avg_risk_score: float
    tier_distribution: Dict[str, int]

class FamilyRollupResponse(BaseModel):
    items: List[FamilyRollupItem]

def get_tier(score: float) -> str:
    if score >= 0.95:
        return "Tier 1"
    elif score >= 0.90:
        return "Tier 2"
    elif score >= 0.85:
        return "Tier 3"
    elif score >= 0.80:
        return "Tier 4"
    elif score >= 0.75:
        return "Tier 5"
    else:
        return "Tier 6"

@router.get("/family-rollup", response_model=FamilyRollupResponse)
async def get_family_rollup(
    min_server_count: int = Query(2),
    session: Session = Depends(get_session)
):
    subquery = (
        session.query(
            MCPServerRegistry.family,
            func.count(MCPServerRegistry.id).label("server_count"),
            func.avg(MCPLLMAxisScores.p_top).label("avg_risk_score")
        )
        .join(MCPLLMAxisScores, MCPServerRegistry.id == MCPLLMAxisScores.server_id)
        .group_by(MCPServerRegistry.family)
        .having(func.count(MCPServerRegistry.id) >= min_server_count)
        .subquery()
    )

    tier_counts = (
        session.query(
            subquery.c.family,
            subquery.c.server_count,
            subquery.c.avg_risk_score,
            func.sum(case(
                (subquery.c.avg_risk_score >= 0.95, 1),
                else_=0
            )).label("tier_1"),
            func.sum(case(
                ((subquery.c.avg_risk_score >= 0.90) & (subquery.c.avg_risk_score < 0.95), 1),
                else_=0
            )).label("tier_2"),
            func.sum(case(
                ((subquery.c.avg_risk_score >= 0.85) & (subquery.c.avg_risk_score < 0.90), 1),
                else_=0
            )).label("tier_3"),
            func.sum(case(
                ((subquery.c.avg_risk_score >= 0.80) & (subquery.c.avg_risk_score < 0.85), 1),
                else_=0
            )).label("tier_4"),
            func.sum(case(
                ((subquery.c.avg_risk_score >= 0.75) & (subquery.c.avg_risk_score < 0.80), 1),
                else_=0
            )).label("tier_5"),
            func.sum(case(
                (subquery.c.avg_risk_score < 0.75, 1),
                else_=0
            )).label("tier_6")
        )
        .group_by(subquery.c.family, subquery.c.server_count, subquery.c.avg_risk_score)
        .all()
    )

    result = []
    for row in tier_counts:
        result.append({
            "family": row.family,
            "server_count": row.server_count,
            "avg_risk_score": float(row.avg_risk_score),
            "tier_distribution": {
                "Tier 1": row.tier_1,
                "Tier 2": row.tier_2,
                "Tier 3": row.tier_3,
                "Tier 4": row.tier_4,
                "Tier 5": row.tier_5,
                "Tier 6": row.tier_6
            }
        })

    return {"items": result}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Add test data
    test_session = TestSession()
    test_server1 = MCPServerRegistry(id=1, family="test_family1")
    test_server2 = MCPServerRegistry(id=2, family="test_family1")
    test_server3 = MCPServerRegistry(id=3, family="test_family2")
    test_score1 = MCPLLMAxisScores(server_id=1, p_top=0.96)
    test_score2 = MCPLLMAxisScores(server_id=2, p_top=0.85)
    test_score3 = MCPLLMAxisScores(server_id=3, p_top=0.70)
    test_session.add_all([test_server1, test_server2, test_server3, test_score1, test_score2, test_score3])
    test_session.commit()

    response = client.get("/family-rollup")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0
    assert any(item["server_count"] >= 1 for item in data["items"])
    assert all("tier_distribution" in item for item in data["items"])
    assert all(
        set(item["tier_distribution"].keys()) == {"Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6"}
        for item in data["items"]
    )

    print("PASS")