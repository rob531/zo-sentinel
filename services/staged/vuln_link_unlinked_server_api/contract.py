import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpServerRegistry, VulnLink

router = APIRouter(prefix="/api")


class UnlinkedServerItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    last_seen: Optional[datetime.datetime] = None


class UnlinkedServerResponse(BaseModel):
    total_servers: int
    linked_servers: int
    unlinked_servers: int
    coverage_pct: float
    unlinked: List[UnlinkedServerItem]
    breakdown: Dict[str, int]


@router.get(
    "/vuln/unlinked-servers",
    response_model=UnlinkedServerResponse,
    tags=["vuln"],
    summary="List MCP servers that have no vulnerability links",
)
def get_unlinked_servers(session: Session = Depends(get_session)):
    # total servers
    total_servers = session.query(func.count()).select_from(McpServerRegistry).scalar() or 0

    # subquery of linked server ids
    linked_subq = (
        session.query(VulnLink.server_id)
        .distinct()
        .subquery()
    )

    # linked servers count
    linked_servers = (
        session.query(func.count())
        .select_from(McpServerRegistry)
        .filter(McpServerRegistry.server_id.in_(linked_subq))
        .scalar()
        or 0
    )

    unlinked_servers = total_servers - linked_servers
    coverage_pct = (linked_servers / total_servers * 100.0) if total_servers else 0.0

    # fetch unlinked server details (limit 100, most recent first)
    unlinked_q = (
        session.query(McpServerRegistry)
        .filter(~McpServerRegistry.server_id.in_(linked_subq))
        .order_by(McpServerRegistry.last_seen.desc())
        .limit(100)
    )
    unlinked_items = [
        UnlinkedServerItem(
            server_id=row.server_id,
            name=row.name,
            verdict=row.verdict,
            risk_tier=row.risk_tier,
            last_seen=row.last_seen,
        )
        for row in unlinked_q
    ]

    # breakdown by registry_source
    breakdown_q = (
        session.query(McpServerRegistry.registry_source, func.count())
        .filter(~McpServerRegistry.server_id.in_(linked_subq))
        .group_by(McpServerRegistry.registry_source)
    )
    breakdown = {src: cnt for src, cnt in breakdown_q}

    return UnlinkedServerResponse(
        total_servers=total_servers,
        linked_servers=linked_servers,
        unlinked_servers=unlinked_servers,
        coverage_pct=round(coverage_pct, 2),
        unlinked=unlinked_items,
        breakdown=breakdown,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.vuln_link_unlinked_server_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a temporary in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Dependency override for the test app
    def get_test_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    now = datetime.datetime.utcnow()
    with SessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id=f"s{i}",
                name=f"Server {i}",
                verdict="clean",
                risk_tier="low",
                last_seen=now - datetime.timedelta(days=i),
                registry_source="srcA",
            )
            for i in range(1, 6)
        ]
        db.add_all(servers)

        links = [
            VulnLink(server_id="s1", advisory_id=1, match_basis="test", match_confidence=1.0, match_value="v1"),
            VulnLink(server_id="s2", advisory_id=2, match_basis="test", match_confidence=1.0, match_value="v2"),
        ]
        db.add_all(links)
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/vuln/unlinked-servers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["total_servers"] == 5, "total_servers mismatch"
    assert data["linked_servers"] == 2, "linked_servers mismatch"
    assert data["unlinked_servers"] == 3, "unlinked_servers mismatch"
    assert data["coverage_pct"] == 40.0, f"coverage_pct mismatch: {data['coverage_pct']}"
    print("PASS")