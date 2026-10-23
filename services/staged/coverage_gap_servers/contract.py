# services/staged/coverage_gap_servers/contract.py
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

router = APIRouter(prefix="/api", tags=["coverage_gap_servers"])


class ServerSummary(BaseModel):
    server_id: str
    name: str
    url: str
    registry_source: str
    first_seen: str


class CoverageGapResponse(BaseModel):
    count: int
    servers: List[ServerSummary]


def get_coverage_gaps(db: Session) -> CoverageGapResponse:
    query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.registry_source,
            McpServerRegistry.first_seen,
        )
        .filter(
            (McpServerRegistry.risk_tier == None)
            | (McpServerRegistry.risk_tier == "")
        )
    )
    rows = query.all()
    servers = [
        ServerSummary(
            server_id=r.server_id,
            name=r.name,
            url=r.url,
            registry_source=r.registry_source,
            first_seen=r.first_seen.isoformat() if r.first_seen else "",
        )
        for r in rows
    ]
    return CoverageGapResponse(count=len(servers), servers=servers)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/servers/coverage-gaps", response_model=CoverageGapResponse)
def list_coverage_gaps_endpoint(db: Session = Depends(get_session)):
    return get_coverage_gaps(db)


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    app = create_app()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200

    db = TestingSessionLocal()

    servers_to_seed = [
        {
            "server_id": "srv-001",
            "name": "Scored Alpha",
            "url": "https://alpha.example.com",
            "registry_source": "registry-a",
            "first_seen": "2025-01-01T00:00:00",
            "risk_tier": "low",
        },
        {
            "server_id": "srv-002",
            "name": "Scored Beta",
            "url": "https://beta.example.com",
            "registry_source": "registry-b",
            "first_seen": "2025-01-02T00:00:00",
            "risk_tier": "high",
        },
        {
            "server_id": "srv-003",
            "name": "Unscored Gamma",
            "url": "https://gamma.example.com",
            "registry_source": "registry-c",
            "first_seen": "2025-01-03T00:00:00",
            "risk_tier": None,
        },
    ]

    for srv in servers_to_seed:
        from datetime import datetime
        from app.models import McpServerRegistry as ASR
        row = ASR(
            server_id=srv["server_id"],
            name=srv["name"],
            url=srv["url"],
            registry_source=srv["registry_source"],
            first_seen=datetime.fromisoformat(srv["first_seen"]) if srv["first_seen"] else None,
            risk_tier=srv["risk_tier"],
        )
        db.add(row)
    db.commit()

    with TestClient(app) as client:
        response = client.get("/api/servers/coverage-gaps")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1, f"Expected count=1, got {data['count']}"
        found = False
        for s in data["servers"]:
            if s["server_id"] == "srv-003":
                found = True
                break
        assert found, f"Expected server_id srv-003 in results: {data['servers']}"

    db.close()
    print("PASS")