from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy import func, or_, and_

class ServerSearchResult(BaseModel):
    server_id: str
    name: str
    registry_source: str
    url: str
    trust_score: float
    verdict: str
    risk_tier: str
    scan_count: int
    last_scanned: str

class ServerSearchResponse(BaseModel):
    items: List[ServerSearchResult]
    total: int
    page: int
    page_size: int
    pages: int

def get_server_search(
    db: Session = Depends(get_session),
    q: Optional[str] = None,
    registry_source: Optional[str] = None,
    risk_tier: Optional[str] = None,
    verdict: Optional[str] = None,
    min_trust_score: Optional[float] = None,
    max_trust_score: Optional[float] = None,
    min_scan_count: Optional[int] = None,
    page: int = 1,
    page_size: int = 50
) -> ServerSearchResponse:
    query = db.query(McpServerRegistry)

    if q:
        query = query.filter(
            or_(
                McpServerRegistry.name.like(f"%{q}%"),
                McpServerRegistry.description.like(f"%{q}%")
            )
        )

    if registry_source:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    if verdict:
        query = query.filter(McpServerRegistry.verdict == verdict)

    if min_trust_score is not None:
        query = query.filter(McpServerRegistry.trust_score >= min_trust_score)

    if max_trust_score is not None:
        query = query.filter(McpServerRegistry.trust_score <= max_trust_score)

    if min_scan_count is not None:
        query = query.filter(McpServerRegistry.scan_count >= min_scan_count)

    total = query.count()

    if page_size > 200:
        page_size = 200

    offset = (page - 1) * page_size
    servers = query.offset(offset).limit(page_size).all()

    pages = (total + page_size - 1) // page_size

    return ServerSearchResponse(
        items=[
            ServerSearchResult(
                server_id=server.server_id,
                name=server.name,
                registry_source=server.registry_source,
                url=server.url,
                trust_score=server.trust_score,
                verdict=server.verdict,
                risk_tier=server.risk_tier,
                scan_count=server.scan_count,
                last_scanned=server.last_scanned.isoformat() if server.last_scanned else None
            ) for server in servers
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages
    )

def create_app():
    app = FastAPI()

    @app.get("/api/servers/search", response_model=ServerSearchResponse)
    async def search_servers(
        q: Optional[str] = Query(None),
        registry_source: Optional[str] = Query(None),
        risk_tier: Optional[str] = Query(None),
        verdict: Optional[str] = Query(None),
        min_trust_score: Optional[float] = Query(None),
        max_trust_score: Optional[float] = Query(None),
        min_scan_count: Optional[int] = Query(None),
        page: int = Query(1),
        page_size: int = Query(50),
        db: Session = Depends(get_session)
    ):
        return get_server_search(
            db=db,
            q=q,
            registry_source=registry_source,
            risk_tier=risk_tier,
            verdict=verdict,
            min_trust_score=min_trust_score,
            max_trust_score=max_trust_score,
            min_scan_count=min_scan_count,
            page=page,
            page_size=page_size
        )

    return app

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    from app.models import McpServerRegistry
    from datetime import datetime

    test_servers = [
        McpServerRegistry(
            server_id="1",
            name="Test Server 1",
            registry_source="source1",
            url="http://test1.example.com",
            description="Description for test server 1",
            trust_score=0.9,
            verdict="CLEAN",
            risk_tier="TRUSTED_GENERAL",
            scan_count=10,
            last_scanned=datetime.now(),
            last_assessed=datetime.now(),
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="2",
            name="Test Server 2",
            registry_source="source1",
            url="http://test2.example.com",
            description="Description for test server 2",
            trust_score=0.7,
            verdict="CLEAN",
            risk_tier="TRUSTED_GENERAL",
            scan_count=5,
            last_scanned=datetime.now(),
            last_assessed=datetime.now(),
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="3",
            name="Test Server 3",
            registry_source="source2",
            url="http://test3.example.com",
            description="Description for test server 3",
            trust_score=0.5,
            verdict="SUSPICIOUS",
            risk_tier="UNTRUSTED",
            scan_count=2,
            last_scanned=datetime.now(),
            last_assessed=datetime.now(),
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="4",
            name="Test Server 4",
            registry_source="source2",
            url="http://test4.example.com",
            description="Description for test server 4",
            trust_score=0.8,
            verdict="CLEAN",
            risk_tier="TRUSTED_GENERAL",
            scan_count=8,
            last_scanned=datetime.now(),
            last_assessed=datetime.now(),
            first_seen=datetime.now(),
            last_seen=datetime.now()
        ),
        McpServerRegistry(
            server_id="5",
            name="Test Server 5",
            registry_source="source3",
            url="http://test5.example.com",
            description="Description for test server 5",
            trust_score=0.6,
            verdict="CLEAN",
            risk_tier="TRUSTED_GENERAL",
            scan_count=3,
            last_scanned=datetime.now(),
            last_assessed=datetime.now(),
            first_seen=datetime.now(),
            last_seen=datetime.now()
        )
    ]

    with SessionLocal() as db:
        for server in test_servers:
            db.add(server)
        db.commit()

    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/servers/search")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5
    assert data["page"] == 1
    assert data["page_size"] == 50
    assert data["pages"] == 1

    response = client.get("/api/servers/search?risk_tier=TRUSTED_GENERAL")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4
    assert len(data["items"]) == 4
    assert all(server["risk_tier"] == "TRUSTED_GENERAL" for server in data["items"])

    print("PASS")