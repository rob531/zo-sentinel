from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


class PerspectiveSnapshotRollup(BaseModel):
    perspective_id: str
    name: str
    snapshot_count: int
    latest_taken_at: Optional[datetime]
    latest_member_count: int
    member_delta: int


class SnapshotRollupResponse(BaseModel):
    total_perspectives: int
    snapshots_taken: int
    perspectives: List[PerspectiveSnapshotRollup]
    generated_at: datetime


router = APIRouter(prefix="/api")


def _membership_member_count(membership: Any) -> int:
    if membership is None:
        return 0

    if isinstance(membership, bytes):
        try:
            membership = membership.decode("utf-8")
        except UnicodeDecodeError:
            return 0

    if isinstance(membership, str):
        try:
            membership = json.loads(membership)
        except (TypeError, ValueError):
            return 0

    if isinstance(membership, (list, tuple, set)):
        return len(membership)

    if isinstance(membership, dict):
        server_ids = membership.get("server_ids")
        if isinstance(server_ids, (list, tuple, set)):
            return len(server_ids)
        return len(membership)

    return 0


@router.get(
    "/perspectives/snapshot-rollup",
    response_model=SnapshotRollupResponse,
)
def get_snapshot_membership_rollup(session=Depends(get_session)) -> SnapshotRollupResponse:
    perspectives = session.query(Perspective).order_by(Perspective.id.asc()).all()
    snapshots = (
        session.query(PerspectiveSnapshot)
        .order_by(
            PerspectiveSnapshot.perspective_id.asc(),
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
        .all()
    )

    snapshots_by_perspective: Dict[Any, List[PerspectiveSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        snapshots_by_perspective[snapshot.perspective_id].append(snapshot)

    rollups: List[PerspectiveSnapshotRollup] = []
    for perspective in perspectives:
        perspective_snapshots = snapshots_by_perspective.get(perspective.id, [])
        latest = perspective_snapshots[0] if perspective_snapshots else None
        previous = perspective_snapshots[1] if len(perspective_snapshots) > 1 else None

        latest_member_count = _membership_member_count(latest.membership) if latest else 0
        previous_member_count = (
            _membership_member_count(previous.membership) if previous else 0
        )

        rollups.append(
            PerspectiveSnapshotRollup(
                perspective_id=str(perspective.id),
                name=perspective.name,
                snapshot_count=len(perspective_snapshots),
                latest_taken_at=latest.taken_at if latest else None,
                latest_member_count=latest_member_count,
                member_delta=latest_member_count - previous_member_count if previous else 0,
            )
        )

    return SnapshotRollupResponse(
        total_perspectives=len(perspectives),
        snapshots_taken=len(snapshots),
        perspectives=rollups,
        generated_at=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    from datetime import timedelta
    from uuid import UUID, uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(test_engine)
    PerspectiveSnapshot.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    integer_ids: Dict[str, int] = defaultdict(int)

    def compatible_id(column):
        try:
            python_type = column.type.python_type
        except (AttributeError, NotImplementedError):
            python_type = str

        if python_type is int:
            integer_ids[column.key] += 1
            return integer_ids[column.key]
        if python_type is UUID:
            return uuid4()
        return str(uuid4())

    test_session = TestSession()
    now = datetime.now(timezone.utc)

    perspective_1 = Perspective(
        id=compatible_id(Perspective.__table__.c.id),
        org_id=compatible_id(Perspective.__table__.c.org_id),
        name="Changing perspective",
        description="Snapshot membership changes",
        facet_filters={},
        created_by=compatible_id(Perspective.__table__.c.created_by),
        created_at=now,
        updated_at=now,
    )
    perspective_2 = Perspective(
        id=compatible_id(Perspective.__table__.c.id),
        org_id=compatible_id(Perspective.__table__.c.org_id),
        name="Stable perspective one",
        description="One snapshot",
        facet_filters={},
        created_by=compatible_id(Perspective.__table__.c.created_by),
        created_at=now,
        updated_at=now,
    )
    perspective_3 = Perspective(
        id=compatible_id(Perspective.__table__.c.id),
        org_id=compatible_id(Perspective.__table__.c.org_id),
        name="Stable perspective two",
        description="One snapshot",
        facet_filters={},
        created_by=compatible_id(Perspective.__table__.c.created_by),
        created_at=now,
        updated_at=now,
    )
    test_session.add_all([perspective_1, perspective_2, perspective_3])
    test_session.flush()

    test_session.add_all(
        [
            PerspectiveSnapshot(
                id=compatible_id(PerspectiveSnapshot.__table__.c.id),
                perspective_id=perspective_1.id,
                taken_at=now - timedelta(minutes=2),
                membership=["server-1"],
            ),
            PerspectiveSnapshot(
                id=compatible_id(PerspectiveSnapshot.__table__.c.id),
                perspective_id=perspective_1.id,
                taken_at=now - timedelta(minutes=1),
                membership=["server-1", "server-2"],
            ),
            PerspectiveSnapshot(
                id=compatible_id(PerspectiveSnapshot.__table__.c.id),
                perspective_id=perspective_2.id,
                taken_at=now - timedelta(minutes=1),
                membership={"server-1": "server-1"},
            ),
            PerspectiveSnapshot(
                id=compatible_id(PerspectiveSnapshot.__table__.c.id),
                perspective_id=perspective_3.id,
                taken_at=now - timedelta(minutes=1),
                membership=["server-1", "server-2", "server-3"],
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/snapshot-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_perspectives"] == 3
        assert payload["snapshots_taken"] == 4

        rollups = {
            item["perspective_id"]: item for item in payload["perspectives"]
        }
        assert rollups[str(perspective_1.id)]["member_delta"] == 1
        assert rollups[str(perspective_2.id)]["member_delta"] == 0
        assert rollups[str(perspective_3.id)]["member_delta"] == 0

    test_session.close()
    test_engine.dispose()
    print("PASS")