# services/staged/risk_tier_snapshot/logic.py
"""
Risk Tier Snapshot Service
Computes risk tier assessment from MCP axis scores.
"""
from datetime import datetime, timedelta
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from test_tools import TestClient  # noqa: F401 - used in router

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk", tags=["risk"])


class AxisScore(BaseModel):
    """Individual axis score representation."""
    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None

    class Config:
        from_attributes = True


class RiskTierSnapshot(BaseModel):
    """Risk tier snapshot response."""
    server_id: int
    name: str
    risk_tier: str
    composite_score: Optional[float] = None
    axes: list[AxisScore]

    class Config:
        from_attributes = True


RISK_TIERS = {
    "minimal": {"min": 0.0, "max": 0.2},
    "low": {"min": 0.2, "max": 0.4},
    "moderate": {"min": 0.4, "max": 0.6},
    "elevated": {"min": 0.6, "max": 0.8},
    "high": {"min": 0.8, "max": 1.0},
    "critical": {"min": 1.0, "max": 2.0},
}


def derive_risk_tier(composite_score: Optional[float]) -> str:
    """Derive risk tier string from composite score."""
    if composite_score is None:
        return "unknown"
    
    for tier_name, bounds in RISK_TIERS.items():
        if bounds["min"] <= composite_score < bounds["max"]:
            return tier_name
    
    if composite_score >= 1.0:
        return "critical"
    
    return "unknown"


async def get_axis_scores_for_server(
    session: AsyncSession,
    server_id: int,
    days: int = 7
) -> list[dict]:
    """
    Fetch axis scores for a server, returning the most recent day's data.
    Uses raw SQL for Postgres portability.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    query = text("""
        SELECT 
            a.axis_name,
            a.p_top,
            a.p_critical,
            a.p_danger,
            a.score_value,
            a.calculated_at
        FROM McpLlmAxisScore a
        WHERE a.server_id = :server_id
          AND a.calculated_at >= :cutoff_date
        ORDER BY a.calculated_at DESC, a.axis_name
    """)
    
    result = await session.execute(
        query,
        {"server_id": server_id, "cutoff_date": cutoff_date}
    )
    rows = result.fetchall()
    
    axis_data = {}
    for row in rows:
        axis_name = row.axis_name
        if axis_name not in axis_data:
            axis_data[axis_name] = {
                "axis_name": axis_name,
                "label": axis_name.replace("_", " ").title(),
                "p_top": float(row.p_top) if row.p_top else None,
                "p_critical": float(row.p_critical) if row.p_critical else None,
                "p_danger": float(row.p_danger) if row.p_danger else None,
            }
    
    return list(axis_data.values())


async def calculate_composite_score(axes: list[dict]) -> Optional[float]:
    """Calculate composite score from axis values."""
    if not axes:
        return None
    
    score_sum = 0.0
    score_count = 0
    
    for axis in axes:
        if axis.get("p_top") is not None:
            score_sum += axis["p_top"]
            score_count += 1
        elif axis.get("p_critical") is not None:
            score_sum += axis["p_critical"]
            score_count += 1
        elif axis.get("p_danger") is not None:
            score_sum += axis["p_danger"]
            score_count += 1
    
    if score_count == 0:
        return None
    
    return score_sum / score_count


@router.get("/tier/{server_id}", response_model=RiskTierSnapshot)
async def get_risk_tier(
    server_id: int,
    session: AsyncSession = Depends(get_session)
) -> RiskTierSnapshot:
    """
    Get risk tier snapshot for a specific server.
    
    - Fetches server info from McpServerRegistry
    - Retrieves axis scores from McpLlmAxisScore
    - Derives composite risk score and tier classification
    """
    server_query = text("""
        SELECT id, name, description, is_active, created_at
        FROM McpServerRegistry
        WHERE id = :server_id
    """)
    
    server_result = await session.execute(
        server_query, 
        {"server_id": server_id}
    )
    server_row = server_result.fetchone()
    
    if not server_row:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    axes = await get_axis_scores_for_server(session, server_id)
    composite_score = await calculate_composite_score(axes)
    risk_tier = derive_risk_tier(composite_score)
    
    axis_models = [
        AxisScore(
            axis_name=a["axis_name"],
            label=a["label"],
            p_top=a.get("p_top"),
            p_critical=a.get("p_critical"),
            p_danger=a.get("p_danger"),
        )
        for a in axes
    ]
    
    return RiskTierSnapshot(
        server_id=server_id,
        name=server_row.name or f"Server-{server_id}",
        risk_tier=risk_tier,
        composite_score=composite_score,
        axes=axis_models,
    )


if __name__ == "__main__":
    import asyncio
    import os
    import sys
    
    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))))
    
    from app.db import get_engine, init_db
    from sqlalchemy.ext.asyncio import create_async_engine
    
    async def run_self_test():
        """Self-test: seed data and verify endpoint."""
        DATABASE_URL = os.environ.get(
            "DATABASE_URL", 
            "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
        )
        
        engine = create_async_engine(DATABASE_URL, echo=False)
        
        async with engine.begin() as conn:
            await conn.run_sync(init_db)
        
        from sqlalchemy.orm import sessionmaker
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        
        async with async_session() as session:
            # Seed test data
            test_servers = [
                {"name": "TestServer-Alpha", "description": "Alpha server"},
                {"name": "TestServer-Beta", "description": "Beta server"},
                {"name": "TestServer-Gamma", "description": "Gamma server"},
            ]
            
            seeded_servers = []
            for srv in test_servers:
                result = await session.execute(
                    text("""
                        INSERT INTO McpServerRegistry (name, description, is_active)
                        VALUES (:name, :description, true)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """),
                    srv
                )
                row = result.fetchone()
                if row:
                    seeded_servers.append(row.id)
                else:
                    select_result = await session.execute(
                        text("SELECT id FROM McpServerRegistry WHERE name = :name"),
                        srv
                    )
                    existing = select_result.fetchone()
                    if existing:
                        seeded_servers.append(existing.id)
            
            if not seeded_servers:
                raise RuntimeError("Failed to seed test servers")
            
            # Seed axis scores for 2 days
            axes = [
                "overall_risk", "auth_strength", "capability_breadth",
                "data_sensitivity", "network_egress", "maintainer_trust",
                "exploit_surface"
            ]
            
            now = datetime.utcnow()
            for days_offset in [0, 1]:
                for server_id in seeded_servers:
                    for axis in axes:
                        await session.execute(
                            text("""
                                INSERT INTO McpLlmAxisScore 
                                (server_id, axis_name, p_top, p_critical, p_danger, 
                                 score_value, calculated_at)
                                VALUES 
                                (:server_id, :axis_name, :p_top, :p_critical, :p_danger,
                                 :score_value, :calculated_at)
                                ON CONFLICT DO NOTHING
                            """),
                            {
                                "server_id": server_id,
                                "axis_name": axis,
                                "p_top": 0.1 + (hash(f"{server_id}{axis}") % 100) / 200,
                                "p_critical": 0.2 + (hash(f"{server_id}{axis}") % 50) / 100,
                                "p_danger": 0.15 + (hash(f"{server_id}{axis}") % 75) / 150,
                                "score_value": 0.5 + (hash(f"{server_id}{axis}") % 100) / 200,
                                "calculated_at": now - timedelta(days=days_offset),
                            }
                        )
            
            await session.commit()
            
            # Test the endpoint logic directly
            test_server_id = seeded_servers[0]
            axes_data = await get_axis_scores_for_server(session, test_server_id)
            composite = await calculate_composite_score(axes_data)
            tier = derive_risk_tier(composite)
            
            # Assertions
            assert len(axes_data) >= 7, f"Expected >=7 axes, got {len(axes_data)}"
            assert tier in RISK_TIERS or tier == "unknown", f"Invalid risk tier: {tier}"
            
            # Test via FastAPI
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router)
            
            with TestClient(app) as client:
                response = client.get(f"/api/risk/tier/{test_server_id}")
                assert response.status_code == 200, f"Expected 200, got {response.status_code}"
                
                data = response.json()
                assert "server_id" in data
                assert "name" in data
                assert "risk_tier" in data
                assert "axes" in data
                assert len(data["axes"]) >= 7, f"Expected >=7 axes in response, got {len(data['axes'])}"
                assert data["risk_tier"] in RISK_TIERS or data["risk_tier"] == "unknown"
            
            print("PASS")
            
        await engine.dispose()
    
    asyncio.run(run_self_test())