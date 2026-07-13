from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

router = APIRouter()

class CriticalServerEntry(BaseModel):
    server_id: str
    name: str
    p_critical: float
    p_top: float
    risk_tier: str
    scored_at: str

class AxisCriticalServersResponse(BaseModel):
    axis: str
    limit: int
    total_matched: int
    servers: List[CriticalServerEntry]

VALID_AXES = {
    'overall_risk',
    'auth_strength',
    'capability_breadth',
    'data_sensitivity',
    'network_egress',
    'maintainer_trust',
    'exploit_surface'
}

@router.get("/servers/critical-axes", response_model=AxisCriticalServersResponse)
async def get_critical_servers_by_axis(
    axis: str = Query(..., description="Risk axis to filter by"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of servers to return"),
    min_p_critical: float = Query(0.0, ge=0.0, le=1.0, description="Minimum p_critical threshold"),
    db: Session = Depends(get_session)
):
    if axis not in VALID_AXES:
        raise HTTPException(status_code=422, detail=f"Invalid axis. Must be one of: {', '.join(VALID_AXES)}")

    subquery = (
        db.query(
            MCPLLMAxisScores.server_id,
            func.max(MCPLLMAxisScores.scored_at).label('latest_score')
        )
        .filter(MCPLLMAxisScores.axis_name == axis)
        .group_by(MCPLLMAxisScores.server_id)
        .subquery()
    )

    query = (
        db.query(
            MCPServerRegistry.id.label('server_id'),
            MCPServerRegistry.name,
            MCPLLMAxisScores.p_critical,
            MCPLLMAxisScores.p_top,
            MCPLLMAxisScores.risk_tier,
            MCPLLMAxisScores.scored_at
        )
        .join(
            MCPLLMAxisScores,
            and_(
                MCPServerRegistry.id == MCPLLMAxisScores.server_id,
                MCPLLMAxisScores.axis_name == axis,
                MCPLLMAxisScores.scored_at == subquery.c.latest_score
            )
        )
        .filter(MCPLLMAxisScores.p_critical >= min_p_critical)
        .order_by(MCPLLMAxisScores.p_critical.desc())
        .limit(limit)
    )

    total_matched = query.count()
    servers = [CriticalServerEntry(**row._asdict()) for row in query.all()]

    return AxisCriticalServersResponse(
        axis=axis,
        limit=limit,
        total_matched=total_matched,
        servers=servers
    )

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    resp = client.get('/servers/critical-axes?axis=exploit_surface&limit=10')
    assert resp.status_code == 200
    data = resp.json()
    assert data['axis'] == 'exploit_surface'
    assert 'servers' in data
    assert isinstance(data['servers'], list)
    assert data['total_matched'] >= 0
    print('PASS: axis_critical_servers_bulk_api smoke')