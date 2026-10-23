from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api")

class TierDistribution(BaseModel):
    tier: int
    count: int
    percentage: float

class TierDistributionResponse(BaseModel):
    tiers: List[TierDistribution]

@router.get("/risk/tier/distribution", response_model=TierDistributionResponse)
def get_risk_tier_distribution(session: Session = Depends(get_session)):
    query = session.query(
        McpLlmAxisScore.c.risk_tier,
        McpServerRegistry.c.server_id
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.c.server_id == McpServerRegistry.c.server_id
    )

    results = query.all()
    total_servers = len(results)

    tier_counts = {}
    for result in results:
        tier = result.risk_tier
        if tier not in tier_counts:
            tier_counts[tier] = 0
        tier_counts[tier] += 1

    tier_distribution = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100
        tier_distribution.append(TierDistribution(tier=tier, count=count, percentage=percentage))

    return TierDistributionResponse(tiers=tier_distribution)

if __name__ == "__main__":
    from sqlalchemy import create_engine, Column, Integer, String, MetaData, Table
    from sqlalchemy.orm import sessionmaker

    engine = create_engine('sqlite:///:memory:')
    metadata = MetaData()

    McpServerRegistry = Table(
        'McpServerRegistry', metadata,
        Column('server_id', Integer, primary_key=True),
        Column('server_name', String)
    )

    McpLlmAxisScore = Table(
        'McpLlmAxisScore', metadata,
        Column('server_id', Integer, primary_key=True),
        Column('risk_tier', Integer)
    )

    metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    session.execute(McpServerRegistry.insert(), [
        {'server_id': 1, 'server_name': 'server1'},
        {'server_id': 2, 'server_name': 'server2'},
        {'server_id': 3, 'server_name': 'server3'}
    ])

    session.execute(McpLlmAxisScore.insert(), [
        {'server_id': 1, 'risk_tier': 1},
        {'server_id': 2, 'risk_tier': 2},
        {'server_id': 3, 'risk_tier': 1}
    ])

    session.commit()

    from fastapi.testclient import TestClient
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    response = client.get("/api/risk/tier/distribution")
    assert response.status_code == 200

    data = response.json()
    assert len(data['tiers']) == 2

    for tier in data['tiers']:
        if tier['tier'] == 1:
            assert tier['count'] == 2

    print("PASS")