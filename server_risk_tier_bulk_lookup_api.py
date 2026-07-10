from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class TierSummary(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    overall_risk_p_top: float
    axes: Dict[str, float]

class BulkTierResponse(BaseModel):
    servers: List[TierSummary]
    total: int
    missing: List[str]

@router.get("/servers/tiers", response_model=BulkTierResponse)
def get_tiers(limit: int = 100, offset: int = 0, db: Session = Depends(get_session)):
    total = db.query(func.count(MCPServerRegistry.server_id)).scalar()
    servers = db.query(MCPServerRegistry).limit(limit).offset(offset).all()

    server_ids = [s.server_id for s in servers]
    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id.in_(server_ids)).all()

    axes_dict = {}
    for score in axis_scores:
        if score.server_id not in axes_dict:
            axes_dict[score.server_id] = {}
        axes_dict[score.server_id][score.axis_name] = score.p_top

    response_servers = []
    for server in servers:
        response_servers.append(TierSummary(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            overall_risk_p_top=server.overall_risk_p_top,
            axes=axes_dict.get(server.server_id, {})
        ))

    return BulkTierResponse(
        servers=response_servers,
        total=total,
        missing=[]
    )

@router.get("/servers/tiers/{server_id}", response_model=TierSummary)
def get_tier(server_id: str, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
    axes_dict = {axis.axis_name: axis.p_top for axis in axes}

    return TierSummary(
        server_id=server.server_id,
        name=server.name,
        risk_tier=server.risk_tier,
        overall_risk_p_top=server.overall_risk_p_top,
        axes=axes_dict
    )

@router.post("/servers/tiers/lookup", response_model=BulkTierResponse)
def bulk_lookup(server_ids: List[str], db: Session = Depends(get_session)):
    servers = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id.in_(server_ids)).all()
    server_ids_set = set(server_ids)
    found_ids = {s.server_id for s in servers}
    missing_ids = list(server_ids_set - found_ids)

    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id.in_(found_ids)).all()
    axes_dict = {}
    for score in axis_scores:
        if score.server_id not in axes_dict:
            axes_dict[score.server_id] = {}
        axes_dict[score.server_id][score.axis_name] = score.p_top

    response_servers = []
    for server in servers:
        response_servers.append(TierSummary(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            overall_risk_p_top=server.overall_risk_p_top,
            axes=axes_dict.get(server.server_id, {})
        ))

    return BulkTierResponse(
        servers=response_servers,
        total=len(servers),
        missing=missing_ids
    )

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Setup in-memory SQLite for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    test_servers = [
        MCPServerRegistry(server_id="srv1", name="Server 1", risk_tier="high", overall_risk_p_top=0.9),
        MCPServerRegistry(server_id="srv2", name="Server 2", risk_tier="medium", overall_risk_p_top=0.7),
        MCPServerRegistry(server_id="srv3", name="Server 3", risk_tier="low", overall_risk_p_top=0.3)
    ]
    test_session.add_all(test_servers)

    axes = ["security", "privacy", "reliability", "performance", "compliance", "cost", "scalability"]
    test_axis_scores = []
    for server in test_servers:
        for axis in axes:
            test_axis_scores.append(MCPLLMAxisScores(
                server_id=server.server_id,
                axis_name=axis,
                p_top=0.5 + (ord(axis[0]) - ord('a')) * 0.1
            ))
    test_session.add_all(test_axis_scores)
    test_session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test POST /servers/tiers/lookup
    response = client.post("/servers/tiers/lookup", json={"server_ids": ["srv1", "srv2", "unknown"]})
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) == 2
    assert len(data["missing"]) == 1
    assert "unknown" in data["missing"]

    # Test GET /servers/tiers
    response = client.get("/servers/tiers")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3

    print("PASS")