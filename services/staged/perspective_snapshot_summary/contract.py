import json
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base, engine  # `engine` is the real SQLAlchemy engine
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api")


class PerspectiveSummaryResponse(BaseModel):
    id: int
    org_id: int
    name: str
    description: str | None = None
    facet_filters: str | None = None
    created_at: datetime
    updated_at: datetime
    taken_at: datetime | None = None
    tier_counts: Dict[str, int] = Field(default_factory=dict)
    total_servers: int = 0


@router.get(
    "/perspectives/{perspective_id}/summary",
    response_model=PerspectiveSummaryResponse,
    name="perspective_snapshot_summary",
)
def get_perspective_summary(
    perspective_id: int, session: Session = Depends(get_session)
) -> PerspectiveSummaryResponse:
    # Fetch perspective metadata
    perspective = session.get(Perspective, perspective_id)
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Fetch latest snapshot for this perspective
    stmt = (
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .limit(1)
    )
    snapshot: PerspectiveSnapshot | None = session.execute(stmt).scalar_one_or_none()

    tier_counts: Dict[str, int] = {}
    taken_at: datetime | None = None

    if snapshot and snapshot.membership:
        # `membership` is stored as JSON; ensure it's a dict
        membership: Any = snapshot.membership
        if isinstance(membership, str):
            try:
                membership = json.loads(membership)
            except json.JSONDecodeError:
                membership = {}
        if isinstance(membership, dict):
            for tier, servers in membership.items():
                # Accept both list of servers or dict containing a list under a key
                if isinstance(servers, dict):
                    servers = servers.get("servers", [])
                if isinstance(servers, list):
                    tier_counts[tier] = len(servers)
                else:
                    tier_counts[tier] = 0
        taken_at = snapshot.taken_at

    total_servers = sum(tier_counts.values())

    return PerspectiveSummaryResponse(
        id=perspective.id,
        org_id=perspective.org_id,
        name=perspective.name,
        description=perspective.description,
        facet_filters=perspective.facet_filters,
        created_at=perspective.created_at,
        updated_at=perspective.updated_at,
        taken_at=taken_at,
        tier_counts=tier_counts,
        total_servers=total_servers,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.perspective_snapshot_summary.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=test_engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        from sqlalchemy.orm import sessionmaker

        SessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with get_test_session() as sess:
        p1 = Perspective(
            id=1,
            org_id=1,
            name="Test Perspective",
            description="A test perspective",
            facet_filters="{}",
            created_by=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        p2 = Perspective(
            id=2,
            org_id=1,
            name="Second Perspective",
            description="Another test perspective",
            facet_filters="{}",
            created_by=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        sess.add_all([p1, p2])
        sess.flush()

        snap1 = PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            taken_at=datetime.utcnow(),
            membership={"tierA": ["srv1", "srv2"], "tierB": ["srv3"]},
        )
        snap2 = PerspectiveSnapshot(
            id=2,
            perspective_id=2,
            taken_at=datetime.utcnow(),
            membership={"tierX": ["srv4"]},
        )
        sess.add_all([snap1, snap2])
        sess.commit()

    # ------------------------------------------------------------------- #
    # Run test client against the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.get("/api/perspectives/1/summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["name"] == "Test Perspective", "Perspective name mismatch"
    assert data["total_servers"] > 0, "Total server count should be non‑zero"

    print("PASS")
    raise SystemExit(0)