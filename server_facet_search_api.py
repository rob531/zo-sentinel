from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
import requests

router = APIRouter()

class ServerSearchResult(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    trust_score: float
    registry_source: str
    last_seen: str

class FacetCounts(BaseModel):
    risk_tier: Dict[str, int]
    verdict: Dict[str, int]
    registry_source: Dict[str, int]

class SearchResponse(BaseModel):
    servers: List[ServerSearchResult]
    facets: FacetCounts
    total: int
    page: int
    page_size: int

def get_write_service_response(query: str, params: dict = None):
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()

def get_facet_counts(db: Session, filters: dict) -> FacetCounts:
    base_query = db.query(MCPServerRegistry)

    for key, value in filters.items():
        if value is not None:
            if key == 'min_trust_score':
                base_query = base_query.filter(MCPServerRegistry.trust_score >= value)
            elif key == 'max_trust_score':
                base_query = base_query.filter(MCPServerRegistry.trust_score <= value)
            else:
                base_query = base_query.filter(getattr(MCPServerRegistry, key) == value)

    risk_tier_counts = base_query.with_entities(
        MCPServerRegistry.risk_tier,
        func.count(MCPServerRegistry.risk_tier)
    ).group_by(MCPServerRegistry.risk_tier).all()

    verdict_counts = base_query.with_entities(
        MCPServerRegistry.verdict,
        func.count(MCPServerRegistry.verdict)
    ).group_by(MCPServerRegistry.verdict).all()

    registry_source_counts = base_query.with_entities(
        MCPServerRegistry.registry_source,
        func.count(MCPServerRegistry.registry_source)
    ).group_by(MCPServerRegistry.registry_source).all()

    return FacetCounts(
        risk_tier={tier: count for tier, count in risk_tier_counts},
        verdict={verdict: count for verdict, count in verdict_counts},
        registry_source={source: count for source, count in registry_source_counts}
    )

@router.get("/servers/search", response_model=SearchResponse)
async def search_servers(
    q: Optional[str] = None,
    risk_tier: Optional[str] = None,
    verdict: Optional[str] = None,
    min_trust_score: Optional[float] = None,
    max_trust_score: Optional[float] = None,
    registry_source: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_session)
):
    filters = {
        'risk_tier': risk_tier,
        'verdict': verdict,
        'min_trust_score': min_trust_score,
        'max_trust_score': max_trust_score,
        'registry_source': registry_source
    }

    query = db.query(MCPServerRegistry)

    for key, value in filters.items():
        if value is not None:
            if key == 'min_trust_score':
                query = query.filter(MCPServerRegistry.trust_score >= value)
            elif key == 'max_trust_score':
                query = query.filter(MCPServerRegistry.trust_score <= value)
            else:
                query = query.filter(getattr(MCPServerRegistry, key) == value)

    if q:
        query = query.filter(
            and_(
                MCPServerRegistry.name.ilike(f"%{q}%"),
                MCPServerRegistry.description.ilike(f"%{q}%")
            )
        )

    total = query.count()

    servers = query.offset((page - 1) * page_size).limit(page_size).all()

    facets = get_facet_counts(db, filters)

    return SearchResponse(
        servers=[ServerSearchResult(**server.__dict__) for server in servers],
        facets=facets,
        total=total,
        page=page,
        page_size=page_size
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    test_data = [
        {
            "server_id": "1",
            "name": "Test Server 1",
            "risk_tier": "HIGH_RISK_ISOLATED",
            "verdict": "TRUSTED_GENERAL",
            "trust_score": 0.8,
            "registry_source": "npm",
            "last_seen": "2023-01-01"
        },
        {
            "server_id": "2",
            "name": "Test Server 2",
            "risk_tier": "MEDIUM_RISK",
            "verdict": "UNTRUSTED",
            "trust_score": 0.5,
            "registry_source": "github",
            "last_seen": "2023-01-02"
        },
        {
            "server_id": "3",
            "name": "Test Server 3",
            "risk_tier": "LOW_RISK",
            "verdict": "TRUSTED_GENERAL",
            "trust_score": 0.9,
            "registry_source": "npm",
            "last_seen": "2023-01-03"
        }
    ]

    MCPServerRegistry.__table__.create(engine)
    session = SessionLocal()
    for data in test_data:
        session.add(MCPServerRegistry(**data))
    session.commit()
    session.close()

    response = client.get("/servers/search")
    assert response.status_code == 200
    assert len(response.json()["servers"]) == 3
    assert "facets" in response.json()
    assert "risk_tier" in response.json()["facets"]
    assert "verdict" in response.json()["facets"]
    print("PASS")