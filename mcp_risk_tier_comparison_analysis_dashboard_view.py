from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy import func, case
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes

router = APIRouter()

class RiskTierComparison(BaseModel):
    tier: str
    server_count: int
    avg_risk_score: float

class RiskTierComparisonResponse(BaseModel):
    tiers: List[RiskTierComparison]

@router.get("/risk-tier-comparison", response_model=RiskTierComparisonResponse)
async def get_risk_tier_comparison(db_session=Depends(get_session)):
    # Calculate risk tier based on axis scores with CRITICAL override
    subquery = (
        db_session.query(
            MCPServerRegistry.server_id,
            case(
                (func.coalesce(MCPLLMAxisScores.critical_axis_score, 0) > 0, "CRITICAL"),
                (func.coalesce(MCPLLMAxisScores.high_axis_score, 0) > 0, "HIGH"),
                (func.coalesce(MCPLLMAxisScores.medium_axis_score, 0) > 0, "MEDIUM"),
                (func.coalesce(MCPLLMAxisScores.low_axis_score, 0) > 0, "LOW"),
                (func.coalesce(MCPLLMAxisScores.negligible_axis_score, 0) > 0, "NEGLIGIBLE"),
                (func.coalesce(MCPLLMAxisScores.none_axis_score, 0) > 0, "NONE"),
                else_="UNKNOWN"
            ).label("risk_tier"),
            func.coalesce(MCPLLMAxisScores.overall_risk_score, 0).label("risk_score")
        )
        .join(MCPLLMAxisScores, MCPServerRegistry.server_id == MCPLLMAxisScores.server_id, isouter=True)
        .subquery()
    )

    # Apply dispute overrides
    final_query = (
        db_session.query(
            case(
                (MCPScoreDisputes.override_tier != None, MCPScoreDisputes.override_tier),
                else_=subquery.c.risk_tier
            ).label("tier"),
            func.count().label("server_count"),
            func.avg(subquery.c.risk_score).label("avg_risk_score")
        )
        .join(subquery, MCPServerRegistry.server_id == subquery.c.server_id, isouter=True)
        .outerjoin(MCPScoreDisputes, MCPServerRegistry.server_id == MCPScoreDisputes.server_id)
        .group_by("tier")
    )

    results = final_query.all()

    return {
        "tiers": [
            {
                "tier": tier,
                "server_count": count,
                "avg_risk_score": round(score, 2) if score is not None else 0.0
            }
            for tier, count, score in results
        ]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
    from sqlalchemy.orm import Session

    # Create in-memory database for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    # Override the session dependency for testing
    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with Session(test_engine) as session:
        # Add test servers
        servers = [
            MCPServerRegistry(server_id=f"server_{i}", name=f"Test Server {i}")
            for i in range(1, 8)
        ]
        session.add_all(servers)

        # Add test axis scores
        axis_scores = [
            MCPLLMAxisScores(
                server_id=f"server_{i}",
                critical_axis_score=1 if i == 1 else 0,
                high_axis_score=1 if i == 2 else 0,
                medium_axis_score=1 if i == 3 else 0,
                low_axis_score=1 if i == 4 else 0,
                negligible_axis_score=1 if i == 5 else 0,
                none_axis_score=1 if i == 6 else 0,
                overall_risk_score=i * 10
            )
            for i in range(1, 7)
        ]
        session.add_all(axis_scores)

        # Add dispute override for one server
        session.add(MCPScoreDisputes(
            server_id="server_7",
            override_tier="CRITICAL"
        ))

        session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk-tier-comparison")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes + override are present
    tiers = {item["tier"] for item in data["tiers"]}
    expected_tiers = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NEGLIGIBLE", "NONE", "UNKNOWN"}
    assert tiers == expected_tiers

    print("PASS")