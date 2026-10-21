from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Set, Union

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import PerspectiveSnapshot


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
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    retained_count: int = 0


router = APIRouter()


def _membership_set(membership: Any) -> Set[str]:
    if isinstance(membership, (str, bytes, bytearray)):
        membership = json.loads(membership)
    if not isinstance(membership, list):
        return set()
    return {str(server_id) for server_id in membership}


@router.get(
    "/api/perspectives/snapshot-compare",
    response_model=Union[SnapshotCompareResponse, UnknownSnapshotResponse],
)
def compare_perspective_snapshots(
    perspective_id: str = Query(..., min_length=1),
    from_id: int = Query(...),
    to_id: int = Query(...),
    session=Depends(get_session),
):
    statement = (
        select(PerspectiveSnapshot)
        .where(
            PerspectiveSnapshot.perspective_id == perspective_id,
            PerspectiveSnapshot.id.in_([from_id, to_id]),
        )
    )
    snapshots = session.execute(statement).scalars().all()
    snapshots_by_id: Dict[int, PerspectiveSnapshot] = {
        snapshot.id: snapshot for snapshot in snapshots
    }

    from_snapshot = snapshots_by_id.get(from_id)
    to_snapshot = snapshots_by_id.get(to_id)
    if from_snapshot is None or to_snapshot is None:
        return {
            "error": "unknown_snapshot",
            "added": [],
            "removed": [],
            "retained_count": 0,
        }

    from_membership = _membership_set(from_snapshot.membership)
    to_membership = _membership_set(to_snapshot.membership)
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
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    snapshot_table = Table(
        "perspective_snapshots",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("perspective_id", String(255), nullable=False),
        Column("taken_at", DateTime, nullable=False),
        Column("membership", String, nullable=False),
    )
    metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            insert(snapshot_table),
            [
                {
                    "id": 10,
                    "perspective_id": "1",
                    "taken_at": datetime(2024, 1, 1, 12, 0, 0),
                    "membership": json.dumps(["srv-a", "srv-b"]),
                },
                {
                    "id": 20,
                    "perspective_id": "1",
                    "taken_at": datetime(2024, 1, 2, 12, 0, 0),
                    "membership": json.dumps(["srv-b", "srv-c"]),
                },
            ],
        )

    test_session = TestSession()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/perspectives/snapshot-compare",
            params={"perspective_id": "1", "from_id": 10, "to_id": 20},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["added"] == ["srv-c"]
        assert payload["removed"] == ["srv-a"]
        assert payload["retained_count"] == 1
        assert payload["added_count"] == 1
        assert payload["removed_count"] == 1

        response = client.get(
            "/api/perspectives/snapshot-compare",
            params={"perspective_id": "1", "from_id": 10, "to_id": 999},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["error"] == "unknown_snapshot"
        assert payload["added"] == []
        assert payload["removed"] == []
        assert payload["retained_count"] == 0

    test_session.close()
    engine.dispose()
    print("PASS")