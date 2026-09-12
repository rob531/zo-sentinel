from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective"])


@router.get("/perspective/health")
def get_perspective_health(db: Session = Depends(get_session)) -> dict[str, Any]:
    total_perspectives = db.query(func.count(Perspective.id)).scalar() or 0
    total_snapshots = db.query(func.count(PerspectiveSnapshot.id)).scalar() or 0

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_snapshots = (
        db.query(func.count(PerspectiveSnapshot.id))
        .filter(PerspectiveSnapshot.taken_at >= thirty_days_ago)
        .scalar()
        or 0
    )

    avg_snapshots = total_snapshots / total_perspectives if total_perspectives > 0 else 0.0

    all_snapshots = db.query(PerspectiveSnapshot).all()
    membership_changes = 0
    prev_membership = {}
    for snap in sorted(all_snapshots, key=lambda s: (s.perspective_id, s.taken_at)):
        if snap.perspective_id in prev_membership:
            if set(snap.membership or []) != set(prev_membership[snap.perspective_id] or []):
                membership_changes += 1
        prev_membership[snap.perspective_id] = snap.membership

    return {
        "total_perspectives": total_perspectives,
        "total_snapshots": total_snapshots,
        "recent_snapshots": recent_snapshots,
        "snapshot_frequency": round(avg_snapshots, 2),
        "membership_changes": membership_changes,
        "healthy": total_perspectives > 0,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from collections.abc import Generator
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session as SASession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session() -> Generator[SASession, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)

    @app.get("/")
    def root():
        return {"status": "ok"}

    app.dependency_overrides[get_session] = override_get_session

    with engine.connect() as conn:
        session = TestingSessionLocal()
        now = datetime.utcnow()
        p = Perspective(
            id=1,
            name="Test Perspective",
            description="Test",
            org_id=1,
            created_by=1,
            facet_filters={},
        )
        session.add(p)
        session.flush()

        s1 = PerspectiveSnapshot(
            perspective_id=p.id,
            membership=[1, 2, 3],
            taken_at=now - timedelta(days=10),
        )
        s2 = PerspectiveSnapshot(
            perspective_id=p.id,
            membership=[1, 2, 3, 4],
            taken_at=now - timedelta(days=5),
        )
        session.add_all([s1, s2])
        session.commit()
        session.close()

    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)
    response = client.get("/api/perspective/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["total_perspectives"] == 1
    assert data["total_snapshots"] == 2
    assert data["membership_changes"] == 1
    print("PASS")