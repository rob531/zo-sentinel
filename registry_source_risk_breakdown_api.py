from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_
import requests
import time
import json

router = APIRouter()

class RiskBreakdown(BaseModel):
    registry_source: str
    total_servers: int
    by_tier: dict
    scanned_within_24h: int

class ResponseModel(BaseModel):
    sources: List[RiskBreakdown]
    generated_at: str

def get_risk_tier_counts(session: Session, registry_source: Optional[str] = None):
    query = session.query(
        MCPServerRegistry.registry_source,
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.id).label('count'),
        func.max(MCPServerRegistry.last_scanned).label('last_scanned')
    ).group_by(
        MCPServerRegistry.registry_source,
        MCPServerRegistry.risk_tier
    )

    if registry_source:
        query = query.filter(MCPServerRegistry.registry_source == registry_source)

    results = query.all()

    # Pivot long to wide format
    sources = {}
    for row in results:
        source = row.registry_source
        tier = row.risk_tier
        count = row.count

        if source not in sources:
            sources[source] = {
                'registry_source': source,
                'total_servers': 0,
                'by_tier': {
                    'TRUSTED_GENERAL': 0,
                    'TRUSTED_RESEARCH': 0,
                    'ENTERPRISE_CONTROLLED': 0,
                    'CAUTION_LIMITED': 0,
                    'HIGH_RISK_ISOLATED': 0,
                    'KNOWN_THREAT': 0,
                    'INSUFFICIENT': 0,
                    'UNKNOWN': 0
                },
                'scanned_within_24h': 0
            }

        sources[source]['total_servers'] += count
        if tier:
            sources[source]['by_tier'][tier] = count
        else:
            sources[source]['by_tier']['UNKNOWN'] = count

    # Calculate scanned_within_24h
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    for source in sources.values():
        scanned_count = session.query(func.count(MCPServerRegistry.id)).filter(
            and_(
                MCPServerRegistry.registry_source == source['registry_source'],
                MCPServerRegistry.last_scanned >= twenty_four_hours_ago
            )
        ).scalar()
        source['scanned_within_24h'] = scanned_count if scanned_count is not None else 0

    return list(sources.values())

@router.get("/registry-sources/risk-breakdown", response_model=ResponseModel)
async def get_risk_breakdown(
    registry_source: Optional[str] = Query(None),
    limit_sources: int = Query(50, le=500),
    session: Session = Depends(get_session)
):
    try:
        results = get_risk_tier_counts(session, registry_source)

        # Apply limit
        if limit_sources < len(results):
            results = results[:limit_sources]

        # Sort by total_servers DESC
        results.sort(key=lambda x: x['total_servers'], reverse=True)

        return {
            "sources": results,
            "generated_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def fake_query_endpoint(query: str):
    # Simulate a query endpoint with exponential backoff
    max_retries = 3
    base_delay = 1
    last_exception = None

    for attempt in range(max_retries):
        try:
            # Simulate a successful response
            time.sleep(base_delay * (2 ** attempt))
            return {"results": []}
        except Exception as e:
            last_exception = e
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))

    raise last_exception

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Set up test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_data = [
        {"registry_source": "source1", "risk_tier": "TRUSTED_GENERAL", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source1", "risk_tier": "TRUSTED_RESEARCH", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source1", "risk_tier": "ENTERPRISE_CONTROLLED", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source1", "risk_tier": None, "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source2", "risk_tier": "CAUTION_LIMITED", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source2", "risk_tier": "HIGH_RISK_ISOLATED", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source2", "risk_tier": "KNOWN_THREAT", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source2", "risk_tier": None, "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source3", "risk_tier": "TRUSTED_GENERAL", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source3", "risk_tier": "TRUSTED_RESEARCH", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source3", "risk_tier": "ENTERPRISE_CONTROLLED", "last_scanned": "2023-01-01T00:00:00Z"},
        {"registry_source": "source3", "risk_tier": None, "last_scanned": "2023-01-01T00:00:00Z"},
    ]

    with TestSession() as session:
        for data in test_data:
            server = MCPServerRegistry(
                registry_source=data["registry_source"],
                risk_tier=data["risk_tier"],
                last_scanned=datetime.fromisoformat(data["last_scanned"])
            )
            session.add(server)
        session.commit()

    # Override the query endpoint for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test the endpoint
    client = TestClient(app)

    # Test without filter
    response = client.get("/registry-sources/risk-breakdown")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sources"]) == 3
    assert data["sources"][0]["registry_source"] == "source1"
    assert data["sources"][0]["total_servers"] == 4
    assert data["sources"][0]["by_tier"]["TRUSTED_GENERAL"] == 1
    assert data["sources"][0]["by_tier"]["TRUSTED_RESEARCH"] == 1
    assert data["sources"][0]["by_tier"]["ENTERPRISE_CONTROLLED"] == 1
    assert data["sources"][0]["by_tier"]["UNKNOWN"] == 1
    assert data["sources"][0]["scanned_within_24h"] == 0

    # Test with filter
    response = client.get("/registry-sources/risk-breakdown?registry_source=source2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sources"]) == 1
    assert data["sources"][0]["registry_source"] == "source2"
    assert data["sources"][0]["total_servers"] == 4
    assert data["sources"][0]["by_tier"]["CAUTION_LIMITED"] == 1
    assert data["sources"][0]["by_tier"]["HIGH_RISK_ISOLATED"] == 1
    assert data["sources"][0]["by_tier"]["KNOWN_THREAT"] == 1
    assert data["sources"][0]["by_tier"]["UNKNOWN"] == 1
    assert data["sources"][0]["scanned_within_24h"] == 0

    print("PASS")