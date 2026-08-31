"""perspective_snapshot_api logic module."""

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


def get_perspective_snapshots(session: Session, perspective_id: int) -> List[dict]:
    """Get snapshots for a perspective with membership counts."""
    stmt = (
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
    )
    results = session.execute(stmt).scalars().all()

    snapshots = []
    for snapshot in results:
        membership = snapshot.membership or {}
        membership_count = len(membership.get("server_ids", [])) if isinstance(membership, dict) else 0

        snapshots.append({
            "id": snapshot.id,
            "perspective_id": snapshot.perspective_id,
            "taken_at": snapshot.taken_at.isoformat(),
            "membership_count": membership_count,
        })

    return snapshots


if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    db = TestingSessionLocal()

    p1 = Perspective(
        id=1,
        name="Test Perspective 1",
        description="Test",
        org_id=1,
        created_by=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        facet_filters={},
    )
    db.add(p1)

    for i in range(3):
        snapshot = PerspectiveSnapshot(
            id=i + 1,
            perspective_id=1,
            taken_at=datetime.utcnow() - timedelta(days=i),
            membership={"server_ids": [1, 2, 3]},
        )
        db.add(snapshot)

    p2 = Perspective(
        id=2,
        name="Test Perspective 2",
        description="Test",
        org_id=1,
        created_by=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        facet_filters={},
    )
    db.add(p2)

    for i in range(3):
        snapshot = PerspectiveSnapshot(
            id=i + 4,
            perspective_id=2,
            taken_at=datetime.utcnow() - timedelta(days=i),
            membership={"server_ids": [4, 5, 6, 7]},
        )
        db.add(snapshot)

    db.commit()
    db.close()

    app = FastAPI()

    class SnapshotResponse(BaseModel):
        id: int
        perspective_id: int
        taken_at: str
        membership_count: int

    @app.get("/api/perspectives/{perspective_id}/snapshots", response_model=List[SnapshotResponse])
    def get_snapshots(perspective_id: int):
        db = TestingSessionLocal()
        try:
            return get_perspective_snapshots(db, perspective_id)
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    import requests

    from multiprocessing import Process

    def run_server():
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8777, log_level="error")

    server = Process(target=run_server)
    server.start()

    import time

    time.sleep(1.5)

    try:
        response = requests.get("http://127.0.0.1:8777/api/perspectives/1/snapshots", timeout=5)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert len(data) == 3, f"Expected 3 snapshots, got {len(data)}"
        print("PASS")
    finally:
        server.terminate()
        server.join()