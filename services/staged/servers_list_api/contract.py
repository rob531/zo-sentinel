from __future__ import annotations

import sys
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ServerResponse(BaseModel):
    server_id: str
    name: str
    registry_source: str
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    risk_tier: Optional[str] = None
    last_scanned: Optional[str] = None
    scan_count: Optional[int] = None


class ServersListResponse(BaseModel):
    servers: List[ServerResponse]
    total: int
    page: int
    page_size: int
    pages: int


def list_servers(
    session: Session,
    page: int = 1,
    page_size: int = 25,
    name: Optional[str] = None,
    registry_source: Optional[str] = None,
    verdict: Optional[str] = None,
    risk_tier: Optional[str] = None,
    sort_by: str = "name",
    sort_dir: str = "asc"
) -> ServersListResponse:
    offset = (page - 1) * page_size

    name_filter = "AND name ILIKE :name" if name else ""
    source_filter = "AND registry_source = :registry_source" if registry_source else ""
    verdict_filter = "AND verdict = :verdict" if verdict else ""
    tier_filter = "AND risk_tier = :risk_tier" if risk_tier else ""

    query = text(f"""
        SELECT server_id, name, registry_source, trust_score, verdict, confidence,
               risk_tier, last_scanned, scan_count
        FROM McpServerRegistry
        WHERE 1=1
        {name_filter}
        {source_filter}
        {verdict_filter}
        {tier_filter}
        ORDER BY {sort_by} {sort_dir}
        LIMIT :page_size OFFSET :offset
    """)

    params = {"page_size": page_size, "offset": offset}
    if name:
        params["name"] = f"%{name}%"
    if registry_source:
        params["registry_source"] = registry_source
    if verdict:
        params["verdict"] = verdict
    if risk_tier:
        params["risk_tier"] = risk_tier

    result = session.execute(query, params)
    rows = result.fetchall()

    servers = [
        ServerResponse(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            trust_score=row.trust_score,
            verdict=row.verdict,
            confidence=row.confidence,
            risk_tier=row.risk_tier,
            last_scanned=str(row.last_scanned) if row.last_scanned else None,
            scan_count=row.scan_count
        )
        for row in rows
    ]

    count_query = text(f"""
        SELECT COUNT(*) as total FROM McpServerRegistry
        WHERE 1=1
        {name_filter}
        {source_filter}
        {verdict_filter}
        {tier_filter}
    """)
    count_result = session.execute(count_query, {k: v for k, v in params.items() if k != "page_size" and k != "offset"})
    total = count_result.scalar()

    pages = (total + page_size - 1) // page_size if total > 0 else 0

    return ServersListResponse(
        servers=servers,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


that_app = FastAPI(lifespan=lifespan)
that_app.add_middleware(
    __import__("fastapi").middleware.cors.CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@that_app.get("/api/servers", response_model=ServersListResponse)
def get_servers(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    name: Optional[str] = None,
    registry_source: Optional[str] = None,
    verdict: Optional[str] = None,
    risk_tier: Optional[str] = None,
    sort_by: str = Query("name", regex="^(name|trust_score|verdict|risk_tier)$"),
    sort_dir: str = Query("asc", regex="^(asc|desc)$"),
    session: Session = Depends(get_session)
):
    return list_servers(
        session=session,
        page=page,
        page_size=page_size,
        name=name,
        registry_source=registry_source,
        verdict=verdict,
        risk_tier=risk_tier,
        sort_by=sort_by,
        sort_dir=sort_dir
    )


def seed_test_db(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS McpServerRegistry"))
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                registry_source VARCHAR,
                url VARCHAR,
                description TEXT,
                trust_score FLOAT,
                verdict VARCHAR,
                confidence FLOAT,
                risk_tier VARCHAR,
                last_scanned TIMESTAMP,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                scan_count INTEGER,
                meta TEXT,
                last_assessed TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry 
            (server_id, name, registry_source, url, description, trust_score, verdict, confidence, risk_tier, last_scanned, first_seen, last_seen, scan_count, meta)
            VALUES 
            ('srv-001', 'Test Server Alpha', 'COMMUNITY', 'https://example.com/alpha', 'A test server', 75.0, 'RECOMMENDED', 0.85, NULL, '2024-01-15 10:00:00', '2024-01-01 00:00:00', '2024-01-15 10:00:00', 10, '{}'),
            ('srv-002', 'Test Server Beta', 'ENTERPRISE', 'https://example.com/beta', 'Another test server', 25.0, 'CAUTION', 0.60, 'HIGH_RISK_ISOLATED', '2024-01-14 09:00:00', '2024-01-02 00:00:00', '2024-01-14 09:00:00', 5, '{}'),
            ('srv-003', 'Test Server Gamma', 'OFFICIAL', 'https://example.com/gamma', 'Third test server', 95.0, 'APPROVED', 0.92, 'TRUSTED', '2024-01-13 08:00:00', '2024-01-03 00:00:00', '2024-01-13 08:00:00', 20, '{}')
        """))


if __name__ == "__main__":
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_url = f"sqlite:///{f.name}"

    engine = create_engine(db_url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _get_test_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    seed_test_db(engine)

    client = TestClient(that_app)
    that_app.dependency_overrides[get_session] = _get_test_session

    try:
        r = client.get("/api/servers")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert len(data["servers"]) == 3, f"Expected 3 servers, got {len(data['servers'])}"
        assert data["total"] == 3
        assert data["page"] == 1

        r = client.get("/api/servers?risk_tier=HIGH_RISK_ISOLATED")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1, f"Expected total=1, got {data['total']}"

        r = client.get("/api/servers?page_size=2")
        assert r.status_code == 200
        data = r.json()
        assert len(data["servers"]) == 2, f"Expected 2 servers, got {len(data['servers'])}"
        assert data["pages"] == 2, f"Expected pages=2, got {data['pages']}"

        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)