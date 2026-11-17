from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


router = APIRouter(prefix="/api")


class HistogramBucket(BaseModel):
    bucket: str
    count: int


class PerspectiveMembership(BaseModel):
    perspective_id: str
    name: str
    member_count: int
    latest_snapshot_at: str | None
    unseen_event_count: int


class MembershipSizeHistogramResponse(BaseModel):
    buckets: list[HistogramBucket]
    total_perspectives: int
    total_members: int
    per_perspective: list[PerspectiveMembership]
    max_members: int


BUCKET_LABELS = ["0", "1-10", "11-50", "51-200", "201+"]


def _member_count(membership) -> int:
    if membership is None:
        return 0
    if isinstance(membership, str):
        return 0
    try:
        return len(membership)
    except TypeError:
        return 0


def _bucket_for_count(member_count: int) -> str:
    if member_count == 0:
        return "0"
    if member_count <= 10:
        return "1-10"
    if member_count <= 50:
        return "11-50"
    if member_count <= 200:
        return "51-200"
    return "201+"


@router.get(
    "/perspectives/membership-size-histogram",
    response_model=MembershipSizeHistogramResponse,
)
def get_membership_size_histogram(
    session: Session = Depends(get_session),
) -> MembershipSizeHistogramResponse:
    perspectives = (
        session.query(Perspective)
        .order_by(Perspective.id)
        .all()
    )
    snapshots = (
        session.query(PerspectiveSnapshot)
        .order_by(
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
        .all()
    )
    events = (
        session.query(PerspectiveEvent)
        .order_by(PerspectiveEvent.id)
        .all()
    )

    latest_snapshot = {}
    for snapshot in snapshots:
        if snapshot.perspective_id not in latest_snapshot:
            latest_snapshot[snapshot.perspective_id] = snapshot

    unseen_events = {}
    for event in events:
        if event.seen is False:
            unseen_events[event.perspective_id] = (
                unseen_events.get(event.perspective_id, 0) + 1
            )

    bucket_counts = {label: 0 for label in BUCKET_LABELS}
    per_perspective = []
    total_members = 0
    max_members = 0

    for perspective in perspectives:
        snapshot = latest_snapshot.get(perspective.id)
        member_count = _member_count(snapshot.membership) if snapshot else 0
        bucket = _bucket_for_count(member_count)

        bucket_counts[bucket] += 1
        total_members += member_count
        max_members = max(max_members, member_count)

        per_perspective.append(
            PerspectiveMembership(
                perspective_id=perspective.id,
                name=perspective.name,
                member_count=member_count,
                latest_snapshot_at=(
                    snapshot.taken_at.isoformat()
                    if snapshot is not None and snapshot.taken_at is not None
                    else None
                ),
                unseen_event_count=unseen_events.get(perspective.id, 0),
            )
        )

    return MembershipSizeHistogramResponse(
        buckets=[
            HistogramBucket(bucket=label, count=bucket_counts[label])
            for label in BUCKET_LABELS
        ],
        total_perspectives=len(perspectives),
        total_members=total_members,
        per_perspective=per_perspective,
        max_members=max_members,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    Perspective.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add_all(
            [
                Perspective(
                    id="perspective-empty",
                    name="Empty perspective",
                    org_id="org-test",
                    created_by="user-test",
                    description="",
                    facet_filters=[],
                    created_at=now,
                    updated_at=now,
                ),
                Perspective(
                    id="perspective-small",
                    name="Small perspective",
                    org_id="org-test",
                    created_by="user-test",
                    description="",
                    facet_filters=[],
                    created_at=now,
                    updated_at=now,
                ),
                Perspective(
                    id="perspective-large",
                    name="Large perspective",
                    org_id="org-test",
                    created_by="user-test",
                    description="",
                    facet_filters=[],
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.add_all(
            [
                PerspectiveSnapshot(
                    perspective_id="perspective-empty",
                    taken_at=now,
                    membership=[],
                ),
                PerspectiveSnapshot(
                    perspective_id="perspective-small",
                    taken_at=now,
                    membership=[f"server-{index}" for index in range(5)],
                ),
                PerspectiveSnapshot(
                    perspective_id="perspective-large",
                    taken_at=now,
                    membership=[f"server-{index}" for index in range(60)],
                ),
            ]
        )
        db.add_all(
            [
                PerspectiveEvent(
                    perspective_id="perspective-small",
                    server_id="server-1",
                    change_type="membership",
                    old_tier=None,
                    new_tier="standard",
                    seen=False,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-large",
                    server_id="server-2",
                    change_type="membership",
                    old_tier=None,
                    new_tier="standard",
                    seen=False,
                    created_at=now,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/membership-size-histogram")
        assert response.status_code == 200
        payload = response.json()

        assert payload["total_perspectives"] == 3
        assert [bucket["bucket"] for bucket in payload["buckets"]] == BUCKET_LABELS
        assert all("count" in bucket for bucket in payload["buckets"])

        large = next(
            item
            for item in payload["per_perspective"]
            if item["perspective_id"] == "perspective-large"
        )
        assert large["member_count"] == 60

        large_bucket = next(
            bucket
            for bucket in payload["buckets"]
            if bucket["bucket"] == "51-200"
        )
        assert large_bucket["count"] == 1

        assert sum(
            item["unseen_event_count"] for item in payload["per_perspective"]
        ) == 2

    print("PASS")