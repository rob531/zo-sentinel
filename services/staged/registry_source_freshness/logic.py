from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceFreshness(BaseModel):
    source: str
    server_count: int
    avg_age_days: float
    max_age_days: float
    freshness_status: str


class FreshnessResponse(BaseModel):
    days: int
    sources: list[SourceFreshness]


def _get_registry_freshness(days: int, session: Session) -> FreshnessResponse:
    """Compute registry source freshness metrics from mcp_server_registry."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    stmt = select(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.server_id).label("server_count"),
        func.avg(
            func.julianday('now') - func.julianday(McpServerRegistry.last_scanned)
        ).label("avg_age_days"),
        func.max(
            func.julianday('now') - func.julianday(McpServerRegistry.last_scanned)
        ).label("max_age_days")
    ).where(
        McpServerRegistry.last_scanned >= cutoff
    ).group_by(McpServerRegistry.registry_source)

    results = session.execute(stmt).all()

    sources = []
    for row in results:
        max_age_days = float(row.max_age_days) if row.max_age_days else 0.0
        sources.append(SourceFreshness(
            source=row.registry_source,
            server_count=row.server_count,
            avg_age_days=round(float(row.avg_age_days) if row.avg_age_days else 0.0, 2),
            max_age_days=round(max_age_days, 2),
            freshness_status="ACTIVE" if max_age_days < days else "STALE"
        ))

    return FreshnessResponse(days=days, sources=sources)


@router.get("/freshness", response_model=FreshnessResponse)
def get_registry_freshness(
    days: int = Query(30, ge=1, description="Number of days to consider for freshness"),
    session: Session = Depends(get_session)
) -> FreshnessResponse:
    """Get freshness metrics for all registry sources.
    
    Returns servers grouped by registry_source with avg_age_days and max_age_days.
    freshness_status is ACTIVE if max_age_days < days else STALE.
    """
    return _get_registry_freshness(days=days, session=session)


if __name__ == "__main__":
    import tempfile
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    from app.models import Base

    # In-memory SQLite for self-test
    db_url = "sqlite:///:memory:"
    engine = create_engine(db_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()

    def override_get_session():
        try:
            yield session
        finally:
            pass

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data: 2 sources with 3 servers each
    now = datetime.utcnow()
    test_data = [
        dict(server_id="s-001", registry_source="source-a", last_scanned=now - timedelta(days=1), name="server-a1"),
        dict(server_id="s-002", registry_source="source-a", last_scanned=now - timedelta(days=1), name="server-a2"),
        dict(server_id="s-003", registry_source="source-a", last_scanned=now - timedelta(days=2), name="server-a3"),
        dict(server_id="s-004", registry_source="source-b", last_scanned=now - timedelta(days=5), name="server-b1"),
        dict(server_id="s-005", registry_source="source-b", last_scanned=now - timedelta(days=10), name="server-b2"),
        dict(server_id="s-006", registry_source="source-b", last_scanned=now - timedelta(days=20), name="server-b3"),
    ]

    for data in test_data:
        session.add(McpServerRegistry(**data))
    session.commit()

    # Test via HTTP
    client = TestClient(app)
    response = client.get("/api/registry/freshness?days=30")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    result = response.json()
    sources = result["sources"]
    assert len(sources) >= 2, f"Expected >= 2 sources, got {len(sources)}"

    for source in sources:
        assert source["freshness_status"] in ("ACTIVE", "STALE"), f"Invalid freshness_status: {source['freshness_status']}"

    print("PASS")