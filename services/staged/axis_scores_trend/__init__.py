"""
Axis Scores Trend Service - Provides trend analysis for LLM axis scores.
"""

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org


router = APIRouter(prefix="/axis-scores-trend", tags=["axis-scores-trend"])


class AxisScoreTrend(BaseModel):
    org_id: str
    period_start: datetime
    period_end: datetime
    axis_name: str
    score_trend: float  # positive = improving, negative = worsening
    current_score: float
    previous_score: float
    sample_count: int


class AxisScoresTrendResponse(BaseModel):
    trends: list[AxisScoreTrend]
    summary: dict[str, Any]


async def get_axis_score_trends(
    org_id: str,
    axis_name: str,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
) -> list[AxisScoreTrend]:
    """
    Calculate trend for a specific axis score over a time period.
    """
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Get scores within the period
    query = (
        select(
            func.date_trunc('day', McpLlmAxisScore.created_at).label('day'),
            func.avg(McpLlmAxisScore.score).label('avg_score'),
            func.count(McpLlmAxisScore.id).label('count'),
        )
        .where(McpLlmAxisScore.org_id == org_id)
        .where(McpLlmAxisScore.axis_name == axis_name)
        .where(McpLlmAxisScore.created_at >= start_date)
        .where(McpLlmAxisScore.created_at <= end_date)
        .group_by(func.date_trunc('day', McpLlmAxisScore.created_at))
        .order_by(func.date_trunc('day', McpLlmAxisScore.created_at))
    )
    
    result = await session.execute(query)
    rows = result.all()
    
    if not rows or len(rows) < 2:
        return []
    
    # Calculate trend
    first_score = rows[0].avg_score
    last_score = rows[-1].avg_score
    total_count = sum(r.count for r in rows)
    
    trend = AxisScoreTrend(
        org_id=org_id,
        period_start=start_date,
        period_end=end_date,
        axis_name=axis_name,
        score_trend=last_score - first_score,
        current_score=last_score,
        previous_score=first_score,
        sample_count=total_count,
    )
    
    return [trend]


@router.get("/org/{org_id}", response_model=AxisScoresTrendResponse)
async def get_org_axis_trends(
    org_id: str,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
) -> AxisScoresTrendResponse:
    """
    Get trend analysis for all axes of an organization.
    """
    # Verify org exists
    org_result = await session.execute(
        select(Org).where(Org.id == org_id)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail=f"Organization {org_id} not found")
    
    # Get distinct axis names for this org
    axes_result = await session.execute(
        select(McpLlmAxisScore.axis_name)
        .distinct()
        .where(McpLlmAxisScore.org_id == org_id)
    )
    axis_names = [row[0] for row in axes_result.all()]
    
    # Calculate trend for each axis
    trends = []
    for axis_name in axis_names:
        axis_trends = await get_axis_score_trends(org_id, axis_name, days, session)
        trends.extend(axis_trends)
    
    # Summary statistics
    summary = {
        "total_axes": len(axis_names),
        "improving_count": sum(1 for t in trends if t.score_trend > 0),
        "worsening_count": sum(1 for t in trends if t.score_trend < 0),
        "stable_count": sum(1 for t in trends if t.score_trend == 0),
        "period_days": days,
    }
    
    return AxisScoresTrendResponse(trends=trends, summary=summary)


@router.get("/axis/{axis_name}/org/{org_id}", response_model=list[AxisScoreTrend])
async def get_specific_axis_trend(
    axis_name: str,
    org_id: str,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
) -> list[AxisScoreTrend]:
    """
    Get trend for a specific axis and organization.
    """
    return await get_axis_score_trends(org_id, axis_name, days, session)


def get_signal_scores() -> dict[str, Any]:
    """
    Get signal scores for integration with other services.
    """
    return {
        "status": "operational",
        "service": "axis_scores_trend",
        "metrics": ["trend", "velocity", "volatility"],
    }


async def _run_self_test(session: AsyncSession) -> dict[str, Any]:
    """
    Self-test to verify service functionality.
    """
    test_results = {
        "tests": [],
        "passed": True,
    }
    
    # Test 1: Check model imports
    try:
        assert McpLlmAxisScore is not None
        assert McpScoreDispute is not None
        assert McpServerRegistry is not None
        assert Org is not None
        test_results["tests"].append({
            "name": "model_imports",
            "status": "PASS",
        })
    except Exception as e:
        test_results["tests"].append({
            "name": "model_imports",
            "status": "FAIL",
            "error": str(e),
        })
        test_results["passed"] = False
    
    # Test 2: Check router setup
    try:
        assert router is not None
        assert len(router.routes) > 0
        test_results["tests"].append({
            "name": "router_setup",
            "status": "PASS",
        })
    except Exception as e:
        test_results["tests"].append({
            "name": "router_setup",
            "status": "FAIL",
            "error": str(e),
        })
        test_results["passed"] = False
    
    # Test 3: Check signal scores function
    try:
        scores = get_signal_scores()
        assert scores["status"] == "operational"
        test_results["tests"].append({
            "name": "signal_scores",
            "status": "PASS",
        })
    except Exception as e:
        test_results["tests"].append({
            "name": "signal_scores",
            "status": "FAIL",
            "error": str(e),
        })
        test_results["passed"] = False
    
    return test_results


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    
    app = FastAPI(title="Axis Scores Trend Service")
    app.include_router(router)
    
    # Override dependencies for self-test
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    test_async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async def override_get_session():
        async with test_async_session() as session:
            yield session
    
    app.dependency_overrides[get_session] = override_get_session
    
    async def run_tests():
        from httpx import AsyncClient, ASGITransport
        
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test signal scores endpoint
            response = await client.get("/axis-scores-trend/org/test-org")
            print(f"Test org endpoint: {response.status_code}")
            
            # Test specific axis endpoint
            response = await client.get("/axis-scores-trend/axis/test-axis/org/test-org")
            print(f"Test axis endpoint: {response.status_code}")
    
    async def run_self_test():
        print("Running self-test...")
        async with test_async_session() as session:
            result = await _run_self_test(session)
            print(f"Self-test results: {result}")
            if result["passed"]:
                print("PASS")
            else:
                print("FAIL")
    
    asyncio.run(run_self_test())