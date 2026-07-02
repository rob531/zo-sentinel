from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores, OrgScope, MCPAxisScoresLatest
import json

router = APIRouter()

@router.get("/perspectives/{id}/servers")
async def get_servers(
    id: str,
    facet_filters: str = None,
    page: int = 1,
    page_size: int = 10,
    session: Session = Depends(get_session)
):
    try:
        facet_filters_dict = json.loads(facet_filters) if facet_filters else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid facet_filters format")

    query = session.query(
        MCPServerRegistry,
        MCPAxisScoresLatest.score,
        MCPAxisScoresLatest.axis,
        MCPAxisScoresLatest.risk_tier
    ).join(
        MCPAxisScoresLatest,
        and_(
            MCPAxisScoresLatest.server_id == MCPServerRegistry.id,
            MCPAxisScoresLatest.is_latest == True
        )
    ).join(
        OrgScope,
        OrgScope.org_id == MCPServerRegistry.org_id
    )

    if 'risk_tier' in facet_filters_dict:
        query = query.filter(MCPAxisScoresLatest.risk_tier.in_(facet_filters_dict['risk_tier']))

    if 'axis' in facet_filters_dict:
        axis_filters = []
        for axis, values in facet_filters_dict['axis'].items():
            axis_filters.append(
                and_(
                    MCPAxisScoresLatest.axis == axis,
                    MCPAxisScoresLatest.score.in_(values)
                )
            )
        query = query.filter(or_(*axis_filters))

    total = query.count()

    servers = query.offset((page - 1) * page_size).limit(page_size).all()

    facet_counts = {}
    for facet in ['risk_tier', 'axis']:
        if facet in facet_filters_dict:
            continue
        if facet == 'risk_tier':
            counts = session.query(
                MCPAxisScoresLatest.risk_tier,
                func.count(MCPAxisScoresLatest.risk_tier)
            ).join(
                MCPServerRegistry,
                MCPAxisScoresLatest.server_id == MCPServerRegistry.id
            ).filter(
                MCPAxisScoresLatest.is_latest == True
            ).group_by(
                MCPAxisScoresLatest.risk_tier
            ).all()
            facet_counts[facet] = {risk_tier: count for risk_tier, count in counts}
        elif facet == 'axis':
            counts = session.query(
                MCPAxisScoresLatest.axis,
                MCPAxisScoresLatest.score,
                func.count(MCPAxisScoresLatest.axis)
            ).join(
                MCPServerRegistry,
                MCPAxisScoresLatest.server_id == MCPServerRegistry.id
            ).filter(
                MCPAxisScoresLatest.is_latest == True
            ).group_by(
                MCPAxisScoresLatest.axis,
                MCPAxisScoresLatest.score
            ).all()
            axis_counts = {}
            for axis, score, count in counts:
                if axis not in axis_counts:
                    axis_counts[axis] = {}
                axis_counts[axis][score] = count
            facet_counts[facet] = axis_counts

    return {
        "servers": [{
            "server": server,
            "score": score,
            "axis": axis,
            "risk_tier": risk_tier
        } for server, score, axis, risk_tier in servers],
        "total": total,
        "facet_counts": facet_counts
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_get_servers():
        facet_filters = json.dumps({"risk_tier": ["HIGH"], "axis": {"auth_strength": ["WEAK"]}})
        response = client.get(f"/perspectives/1/servers?facet_filters={facet_filters}")
        assert response.status_code == 200
        data = response.json()
        assert "servers" in data
        assert "total" in data
        assert "facet_counts" in data
        assert "risk_tier" in data["facet_counts"]
        assert "axis" in data["facet_counts"]

    pytest.main(["-v", __file__])
    print("PASS")