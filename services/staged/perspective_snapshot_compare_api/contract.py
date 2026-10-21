from datetime import datetime, timezone
from typing import List, Union

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


class SnapshotCompareResponse(BaseModel):
    perspective_id: str
    from_id: int
    to_id: int
    from_taken_at: datetime
    to_taken_at: datetime
    added: List[str]
    removed: List[str]
    retained_count: int
    added_count: int
    removed_count: int


class UnknownSnapshotResponse(BaseModel):
    error: str
    added: List[str]
    removed: List[str]
    retained_count: int


def snapshot_compare(
    perspective_id: str = Query(...),
    from_id: int = Query(...),
    to_id: int = Query(...),
    session=Depends(get_session),
):
    snapshots = (
        session.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.perspective_id == perspective_id,
            PerspectiveSnapshot.id.in_((from_id, to_id)),
        )
        .all()
    )
    by_id = {snapshot.id: snapshot for snapshot in snapshots}

    if from_id not in by_id or to_id not in by_id:
        return {
            "error": "unknown_snapshot",
            "added": [],
            "removed": [],
            "retained_count": 0,
        }

    from_snapshot = by_id[from_id]
    to_snapshot = by_id[to_id]
    from_membership = set(from_snapshot.membership or [])
    to_membership = set(to_snapshot.membership or [])

    added = sorted(to_membership - from_membership)
    removed = sorted(from_membership - to_membership)
    retained_count = len(from_membership & to_membership)

    return {
        "perspective_id": perspective_id,
        "from_id": from_id,
        "to_id": to_id,
        "from_taken_at": from_snapshot.taken_at,
        "to_taken_at": to_snapshot.taken_at,
        "added": added,
        "removed": removed,
        "retained_count": retained_count,
        "added_count": len(added),
        "removed_count": len(removed),
    }


app = FastAPI()
app.add_api_route(
    "/api/perspectives/snapshot-compare",
    snapshot_compare,
    methods=["GET"],
    response_model=Union[SnapshotCompareResponse, UnknownSnapshotResponse],
)


if __name__ == "__main__":
    that_app = FastAPI()
    that_app.add_api_route(
        "/api/perspectives/snapshot-compare",
        snapshot_compare,
        methods=["GET"],
        response_model=Union[SnapshotCompareResponse, UnknownSnapshotResponse],
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Perspective.__table__.create(bind=engine)
    PerspectiveSnapshot.__table__.create(bind=engine)

    db = TestingSession()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            Perspective(
                id="perspective-acceptance",
                org_id="org-acceptance",
                name="Acceptance Perspective",
                description="Snapshot comparison acceptance test",
                facet_filters={},
                created_by="acceptance-user",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            PerspectiveSnapshot(
                id=1,
                perspective_id="perspective-acceptance",
                taken_at=now,
                membership=["srv-a", "srv-b"],
            )
        )
        db.add(
            PerspectiveSnapshot(
                id=2,
                perspective_id="perspective-acceptance",
                taken_at=now,
                membership=["srv-b", "srv-c"],
            )
        )
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/perspectives/snapshot-compare",
            params={
                "perspective_id": "perspective-acceptance",
                "from_id": 1,
                "to_id": 2,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["added"] == ["srv-c"], payload
        assert payload["removed"] == ["srv-a"], payload
        assert payload["retained_count"] == 1, payload
        assert payload["added_count"] == 1, payload
        assert payload["removed_count"] == 1, payload

        unknown_response = client.get(
            "/api/perspectives/snapshot-compare",
            params={
                "perspective_id": "perspective-acceptance",
                "from_id": 999,
                "to_id": 2,
            },
        )
        assert unknown_response.status_code == 200, unknown_response.text
        unknown_payload = unknown_response.json()
        assert unknown_payload["error"] == "unknown_snapshot", unknown_payload
        assert unknown_payload["added"] == [], unknown_payload
        assert unknown_payload["removed"] == [], unknown_payload
        assert unknown_payload["retained_count"] == 0, unknown_payload

    print("PASS")