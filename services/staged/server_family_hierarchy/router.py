from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])


class ServerInFamily(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    last_scanned: Optional[str] = None


class TierBreakdown(BaseModel):
    tier: str
    count: int


class FamilyResponse(BaseModel):
    source: str
    total_count: int
    tier_breakdown: dict[str, int]
    servers: list[ServerInFamily]


class FamilyTreeResponse(BaseModel):
    server_id: str
    family: FamilyResponse


@router.get("/servers/family-tree", response_model=FamilyTreeResponse)
def get_family_tree(
    server_id: str = Query(..., description="Server ID to get family tree for"),
    depth: int = Query(3, description="Depth of family tree"),
    session: Session = Depends(get_session),
):
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        return FamilyTreeResponse(
            server_id=server_id,
            family=FamilyResponse(
                source="unknown",
                total_count=0,
                tier_breakdown={},
                servers=[]
            )
        )

    source = server.registry_source or "unknown"
    family_servers = session.query(McpServerRegistry).filter(
        McpServerRegistry.registry_source == source
    ).all()

    tier_breakdown: dict[str, int] = {}
    servers_in_family = []

    for s in family_servers:
        tier = s.risk_tier or "unknown"
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

        servers_in_family.append(ServerInFamily(
            server_id=s.server_id,
            name=s.name or "",
            risk_tier=tier,
            last_scanned=s.last_scanned.isoformat() if s.last_scanned else None
        ))

    return FamilyTreeResponse(
        server_id=server_id,
        family=FamilyResponse(
            source=source,
            total_count=len(family_servers),
            tier_breakdown=tier_breakdown,
            servers=servers_in_family
        )
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime, timezone

    app = FastAPI()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = TestingSessionLocal()

    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    servers = [
        McpServerRegistry(
            server_id="srv-001",
            name="GitHub CLI Tool",
            registry_source="github.com",
            url="https://github.com/example/cli",
            risk_tier="low",
            last_scanned=now,
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="GitHub API Wrapper",
            registry_source="github.com",
            url="https://github.com/example/api",
            risk_tier="medium",
            last_scanned=now,
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="NPM Utils",
            registry_source="npmjs.com",
            url="https://npmjs.com/package/utils",
            risk_tier="high",
            last_scanned=now,
        ),
    ]

    for s in servers:
        session.add(s)
    session.commit()

    app.include_router(router)

    the_app = app
    the_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(the_app)

    response = client.get("/api/servers/family-tree?server_id=srv-001&depth=3")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["server_id"] == "srv-001"
    family = data["family"]

    family_count = 1 if family["source"] == "github.com" else 0
    if family["source"] == "npmjs.com":
        family_count = 1

    all_sources = set()
    if family["source"] == "github.com":
        all_sources.add("github.com")
    elif family["source"] == "npmjs.com":
        all_sources.add("npmjs.com")

    assert len(all_sources) == 1, f"Expected 1 unique family, got {len(all_sources)}"

    assert family["total_count"] == 2, f"Expected 2 servers in github.com family, got {family['total_count']}"

    srv_002_found = any(s["server_id"] == "srv-002" for s in family["servers"])
    assert srv_002_found, "srv-002 should appear in github.com family"

    print("PASS")