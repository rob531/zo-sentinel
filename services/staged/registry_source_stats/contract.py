# services/staged/registry_source_stats/contract.py

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base  # get_session is a dependency that yields a Session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class SourceStat(BaseModel):
    registry_source: str
    server_count: int
    last_seen: Optional[datetime] = None
    first_seen: Optional[datetime] = None


class SourcesResponse(BaseModel):
    sources: List[SourceStat]


@router.get(
    "/registry/sources",
    response_model=SourcesResponse,
    name="registry_source_stats:get_registry_source_stats",
)
def get_registry_source_stats(
    session: Session = Depends(get_session),
) -> SourcesResponse:
    """
    Return per‑registry‑source statistics derived from the
    `mcp_server_registry` table.
    """
    rows = (
        session.query(
            McpServerRegistry.registry_source,
            func.count().label("server_count"),
            func.max(McpServerRegistry.last_seen).label("last_seen"),
            func.min(McpServerRegistry.first_seen).label("first_seen"),
        )
        .group_by(McpServerRegistry.registry_source)
        .all()
    )

    sources = [
        SourceStat(
            registry_source=row.registry_source,
            server_count=row.server_count,
            last_seen=row.last_seen,
            first_seen=row.first_seen,
        )
        for row in rows
    ]

    return SourcesResponse(sources=sources)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_source_stats.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a temporary in‑memory SQLite DB that mimics the real schema.
    # ------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables using the real metadata.
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Dependency override that yields sessions bound to the test engine.
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed the test DB with deterministic data.
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    earlier = datetime(2020, 1, 1)

    seed_rows = [
        McpServerRegistry(
            server_id=1,
            name="srv-a1",
            registry_source="source_a",
            first_seen=earlier,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id=2,
            name="srv-a2",
            registry_source="source_a",
            first_seen=earlier,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id=3,
            name="srv-b1",
            registry_source="source_b",
            first_seen=earlier,
            last_seen=now,
        ),
    ]

    with SessionLocal() as db:
        db.add_all(seed_rows)
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app, inject the override, and run the test client.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/registry/sources")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    try:
        sources = {s["registry_source"]: s["server_count"] for s in payload["sources"]}
        assert sources.get("source_a") == 2, "source_a count mismatch"
        assert sources.get("source_b") == 1, "source_b count mismatch"
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)