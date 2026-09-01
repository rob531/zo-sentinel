"""Server registry list service - queries MCP server registry with risk scoring."""

from typing import Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class ServerListItem(BaseModel):
    server_id: str
    name: str
    registry_source: str
    url: str
    description: Optional[str]
    risk_tier: str
    composite_score: Optional[float]
    last_assessed: Optional[datetime]
    verdict: Optional[str]


class ServerListResponse(BaseModel):
    total: int
    servers: list[ServerListItem]


def get_servers(
    session: Session,
    risk_tier: Optional[str] = None,
    registry_source: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> ServerListResponse:
    """Query MCP server registry with composite risk scores."""
    
    params: dict = {"limit": limit, "offset": offset}
    where_clauses: list[str] = []
    
    if risk_tier:
        where_clauses.append("r.risk_tier = :risk_tier")
        params["risk_tier"] = risk_tier
    
    if registry_source:
        where_clauses.append("r.registry_source = :registry_source")
        params["registry_source"] = registry_source
    
    if search:
        where_clauses.append("(r.name ILIKE :search OR r.description ILIKE :search)")
        params["search"] = f"%{search}%"
    
    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    count_sql = text(f"""
        SELECT COUNT(*) as cnt
        FROM McpServerRegistry r
        {where_sql}
    """)
    
    list_sql = text(f"""
        SELECT 
            r.server_id,
            r.name,
            r.registry_source,
            r.url,
            r.description,
            r.risk_tier,
            r.last_assessed,
            r.verdict,
            s.p_top as composite_score
        FROM McpServerRegistry r
        LEFT JOIN McpLlmAxisScore s 
            ON r.server_id = s.server_id 
            AND s.axis_name = 'overall_risk'
            AND s.is_active = true
        {where_sql}
        ORDER BY r.last_assessed DESC
        LIMIT :limit OFFSET :offset
    """)
    
    count_result = session.execute(count_sql, params).fetchone()
    total = count_result.cnt if count_result else 0
    
    rows = session.execute(list_sql, params).fetchall()
    
    servers = [
        ServerListItem(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            url=row.url,
            description=row.description,
            risk_tier=row.risk_tier,
            composite_score=float(row.composite_score) if row.composite_score is not None else None,
            last_assessed=row.last_assessed,
            verdict=row.verdict,
        )
        for row in rows
    ]
    
    return ServerListResponse(total=total, servers=servers)


# Self-test with in-memory store
if __name__ == "__main__":
    import json
    from unittest.mock import MagicMock, patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    mock_servers = [
        {
            "server_id": "srv-001",
            "name": "Production Server Alpha",
            "registry_source": "enterprise",
            "url": "https://alpha.internal",
            "description": "Production MCP server for critical operations",
            "risk_tier": "critical",
            "last_assessed": datetime(2024, 1, 15, 10, 30, 0),
            "verdict": "approved",
            "composite_score": 0.92,
        },
        {
            "server_id": "srv-002",
            "name": "Beta Sandbox",
            "registry_source": "community",
            "url": "https://beta.community.io",
            "description": "Community sandbox server for testing",
            "risk_tier": "medium",
            "last_assessed": datetime(2024, 1, 14, 8, 0, 0),
            "verdict": "review_required",
            "composite_score": 0.45,
        },
        {
            "server_id": "srv-003",
            "name": "Gamma Analysis Tool",
            "registry_source": "enterprise",
            "url": "https://gamma.internal",
            "description": "Security analysis tooling",
            "risk_tier": "high",
            "last_assessed": datetime(2024, 1, 13, 14, 0, 0),
            "verdict": "restricted",
            "composite_score": 0.28,
        },
        {
            "server_id": "srv-004",
            "name": "Delta Research",
            "registry_source": "community",
            "url": "https://delta.open.dev",
            "description": "Open research and development",
            "risk_tier": "low",
            "last_assessed": datetime(2024, 1, 12, 9, 0, 0),
            "verdict": "approved",
            "composite_score": 0.78,
        },
    ]
    
    class MockRow:
        def __init__(self, data):
            for k, v in data.items():
                setattr(self, k, v)
    
    mock_count_result = MockRow({"cnt": len(mock_servers)})
    mock_rows = [MockRow(s) for s in mock_servers]
    
    def mock_execute(sql, params=None):
        result = MagicMock()
        sql_str = str(sql)
        if "COUNT" in sql_str:
            result.fetchone.return_value = mock_count_result
        else:
            result.fetchall.return_value = mock_rows
        return result
    
    mock_session = MagicMock()
    mock_session.execute = mock_execute
    
    app = FastAPI()
    
    def override_get_session():
        return mock_session
    
    that_app = app
    that_app.dependency_overrides[get_session] = override_get_session
    
    with patch("app.db.get_session", override_get_session):
        response = get_servers(
            session=mock_session,
            risk_tier=None,
            registry_source=None,
            search=None,
            limit=50,
            offset=0,
        )
    
    assert response.total == 4, f"Expected total=4, got {response.total}"
    assert len(response.servers) == 4, f"Expected 4 servers, got {len(response.servers)}"
    
    risk_tiers_found = {s.risk_tier for s in response.servers}
    assert "critical" in risk_tiers_found, f"Expected 'critical' risk_tier in {risk_tiers_found}"
    
    has_composite_score = any(s.composite_score is not None for s in response.servers)
    assert has_composite_score, "Expected at least one server with composite_score"
    
    print("PASS")