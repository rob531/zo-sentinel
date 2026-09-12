"""
Risk Tier Server Detail Service
GET /api/risk/server/{server_id} - Returns detailed risk assessment for a server
"""

from typing import List, Optional
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


router = APIRouter(prefix="/api", tags=["risk"])


class RiskTier(str, Enum):
    """Valid risk tier levels"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKLISTED = "blocklisted"


class AxisScore(BaseModel):
    """Individual risk axis score"""
    axis_name: str
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class OverallRisk(BaseModel):
    """Overall risk summary"""
    label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class ServerRiskDetail(BaseModel):
    """Complete server risk assessment response"""
    server_id: str
    server_name: str
    axes: List[AxisScore]
    overall: OverallRisk
    risk_tier: str
    criteria_version: Optional[str] = None
    last_scored: Optional[datetime] = None


def compute_risk_tier(
    overall_label: str,
    p_critical: Optional[float],
    p_top: Optional[float],
    escalated: bool
) -> str:
    """Compute risk tier based on overall label and probabilities"""
    if overall_label == "blocklisted" or escalated:
        return RiskTier.BLOCKLISTED.value
    if overall_label == "critical" or (p_critical is not None and p_critical >= 0.7):
        return RiskTier.CRITICAL.value
    if overall_label == "high" or (p_critical is not None and p_critical >= 0.4):
        return RiskTier.HIGH.value
    if overall_label == "medium" or (p_top is not None and p_top >= 0.6):
        return RiskTier.MEDIUM.value
    if overall_label == "low" or (p_critical is not None and p_critical >= 0.1):
        return RiskTier.LOW.value
    return RiskTier.NONE.value


async def get_server_risk_detail(
    server_id: str,
    session: AsyncSession
) -> ServerRiskDetail:
    """
    Fetch detailed risk assessment for a server.
    
    Reads all 6 risk axis rows + overall_risk from McpLlmAxisScore,
    joined with McpServerRegistry for server name.
    """
    # Query to get server info and all axis scores
    query = text("""
        SELECT 
            sr.server_id,
            sr.name as server_name,
            s.axis_name,
            s.label,
            s.p_top,
            s.p_critical,
            s.p_danger,
            s.escalated,
            s.criteria_version,
            s.last_scored
        FROM McpLlmAxisScore s
        INNER JOIN McpServerRegistry sr ON s.server_id = sr.server_id
        WHERE s.server_id = :server_id
        ORDER BY 
            CASE s.axis_name
                WHEN 'overall_risk' THEN 1
                WHEN 'auth_strength' THEN 2
                WHEN 'capability_breadth' THEN 3
                WHEN 'data_sensitivity' THEN 4
                WHEN 'network_egress' THEN 5
                WHEN 'maintainer_trust' THEN 6
                WHEN 'exploit_surface' THEN 7
                ELSE 8
            END
    """)
    
    result = await session.execute(query, {"server_id": server_id})
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found or has no risk scores")
    
    # First row contains server info
    first_row = rows[0]
    server_name = first_row.server_name
    criteria_version = first_row.criteria_version
    last_scored = first_row.last_scored
    
    axes: List[AxisScore] = []
    overall: Optional[OverallRisk] = None
    
    for row in rows:
        axis_score = AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=bool(row.escalated) if row.escalated is not None else False
        )
        
        if row.axis_name == "overall_risk":
            overall = OverallRisk(
                label=row.label,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger
            )
        else:
            axes.append(axis_score)
    
    # Compute risk tier from overall risk
    risk_tier = compute_risk_tier(
        overall_label=overall.label if overall else "none",
        p_critical=overall.p_critical if overall else None,
        p_top=overall.p_top if overall else None,
        escalated=any(a.escalated for a in axes)
    )
    
    return ServerRiskDetail(
        server_id=server_id,
        server_name=server_name,
        axes=axes,
        overall=overall or OverallRisk(label="none"),
        risk_tier=risk_tier,
        criteria_version=criteria_version,
        last_scored=last_scored
    )


@router.get("/risk/server/{server_id}", response_model=ServerRiskDetail)
async def get_server_risk(
    server_id: str,
    session: AsyncSession = Depends(get_session)
) -> ServerRiskDetail:
    """
    Get detailed risk assessment for a specific server.
    
    Returns all risk axis scores along with overall risk assessment.
    """
    return await get_server_risk_detail(server_id, session)


# Export for use by other services
__all__ = ["get_server_risk_detail", "ServerRiskDetail", "AxisScore", "OverallRisk", "RiskTier"]


if __name__ == "__main__":
    import asyncio
    from collections import OrderedDict
    
    # In-memory test store for contract verification
    class InMemoryStore:
        def __init__(self):
            self.servers = OrderedDict()
            self.axis_scores = []
    
    store = InMemoryStore()
    
    # Seed 2 servers with 6 axes each
    test_servers = [
        {
            "server_id": "srv_test_001",
            "name": "Test Server Alpha",
            "axes": [
                {"axis_name": "overall_risk", "label": "high", "p_top": 0.1, "p_critical": 0.75, "p_danger": 0.9, "escalated": False},
                {"axis_name": "auth_strength", "label": "medium", "p_top": 0.2, "p_critical": 0.5, "p_danger": 0.7, "escalated": False},
                {"axis_name": "capability_breadth", "label": "low", "p_top": 0.6, "p_critical": 0.2, "p_danger": 0.4, "escalated": False},
                {"axis_name": "data_sensitivity", "label": "critical", "p_top": 0.05, "p_critical": 0.85, "p_danger": 0.95, "escalated": False},
                {"axis_name": "network_egress", "label": "high", "p_top": 0.1, "p_critical": 0.65, "p_danger": 0.8, "escalated": False},
                {"axis_name": "maintainer_trust", "label": "low", "p_top": 0.5, "p_critical": 0.3, "p_danger": 0.6, "escalated": False},
                {"axis_name": "exploit_surface", "label": "medium", "p_top": 0.25, "p_critical": 0.45, "p_danger": 0.65, "escalated": False},
            ]
        },
        {
            "server_id": "srv_test_002",
            "name": "Test Server Beta",
            "axes": [
                {"axis_name": "overall_risk", "label": "medium", "p_top": 0.35, "p_critical": 0.35, "p_danger": 0.6, "escalated": False},
                {"axis_name": "auth_strength", "label": "high", "p_top": 0.7, "p_critical": 0.15, "p_danger": 0.3, "escalated": False},
                {"axis_name": "capability_breadth", "label": "medium", "p_top": 0.4, "p_critical": 0.35, "p_danger": 0.55, "escalated": False},
                {"axis_name": "data_sensitivity", "label": "low", "p_top": 0.7, "p_critical": 0.15, "p_danger": 0.3, "escalated": False},
                {"axis_name": "network_egress", "label": "low", "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.2, "escalated": False},
                {"axis_name": "maintainer_trust", "label": "high", "p_top": 0.75, "p_critical": 0.1, "p_danger": 0.25, "escalated": False},
                {"axis_name": "exploit_surface", "label": "low", "p_top": 0.65, "p_critical": 0.2, "p_danger": 0.4, "escalated": False},
            ]
        }
    ]
    
    for srv in test_servers:
        store.servers[srv["server_id"]] = srv
    
    VALID_TIERS = {"none", "low", "medium", "high", "critical", "blocklisted"}
    
    async def run_tests():
        from unittest.mock import AsyncMock, MagicMock
        
        all_passed = True
        
        for server_data in test_servers:
            server_id = server_data["server_id"]
            
            # Create mock session
            mock_session = AsyncMock()
            
            # Mock the execute result
            mock_result = MagicMock()
            rows_data = []
            
            for axis in server_data["axes"]:
                row = MagicMock()
                row.server_id = server_id
                row.server_name = server_data["name"]
                row.axis_name = axis["axis_name"]
                row.label = axis["label"]
                row.p_top = axis["p_top"]
                row.p_critical = axis["p_critical"]
                row.p_danger = axis["p_danger"]
                row.escalated = axis["escalated"]
                row.criteria_version = "1.0.0"
                row.last_scored = datetime.utcnow()
                rows_data.append(row)
            
            mock_result.fetchall.return_value = rows_data
            mock_session.execute.return_value = mock_result
            
            try:
                result = await get_server_risk_detail(server_id, mock_session)
                
                # Contract assertions
                assert result.server_id == server_id, f"server_id mismatch"
                assert result.server_name == server_data["name"], f"server_name mismatch"
                assert len(result.axes) == 6, f"axes length should be 6, got {len(result.axes)}"
                assert result.risk_tier in VALID_TIERS, f"invalid risk_tier: {result.risk_tier}"
                assert result.overall is not None, "overall should not be None"
                
                print(f"✓ Server {server_id}: axes={len(result.axes)}, tier={result.risk_tier}")
                
            except Exception as e:
                print(f"✗ Server {server_id}: {e}")
                all_passed = False
        
        if all_passed:
            print("PASS")
        else:
            print("FAIL")
        
        return all_passed
    
    asyncio.run(run_tests())