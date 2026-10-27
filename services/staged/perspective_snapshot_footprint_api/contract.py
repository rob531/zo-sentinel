from datetime import datetime, timezone
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


class PerspectiveSnapshotFootprint(BaseModel):
    perspective_id: str
    name: str
    snapshot_count: int
    last_taken_at: Optional[str] = None
    age_days: Optional[float] = None
    newest_member_count: Optional[int] = None


class PerspectiveSnapshotFootprintResponse(BaseModel):
    perspectives: List[PerspectiveSnapshotFootprint]
    total_snapshots: int
    generated_at: str


router = APIRouter(prefix="/api")


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _utc_datetime(value).isoformat()


@router.get(
    "/perspectives/snapshots",
    response_model=PerspectiveSnapshotFootprintResponse,
)
def get_perspective_snapshot_footprints(
    session: Session = Depends(get_session),
) -> PerspectiveSnapshotFootprintResponse:
    statement = (
        select(
            Perspective.id.label("perspective_id"),
            Perspective.name.label("name"),
            PerspectiveSnapshot.id.label("snapshot_id"),
            PerspectiveSnapshot.taken_at.label("taken_at"),
            PerspectiveSnapshot.membership.label("membership"),
        )
        .select_from(Perspective)
        .outerjoin(
            PerspectiveSnapshot,
            PerspectiveSnapshot.perspective_id == Perspective.id,
        )
        .order_by(
            Perspective.id,
            PerspectiveSnapshot.taken_at.desc().nulls_last(),
            PerspectiveSnapshot.id.desc(),
        )
    )
    rows = session.execute(statement).all()

    perspectives: List[PerspectiveSnapshotFootprint] = []
    current_id: Optional[str] = None
    current_name: Optional[str] = None
    snapshot_count = 0
    newest_taken_at: Optional[datetime] = None
    newest_membership = None
    total_snapshots = 0

    def append_current() -> None:
        nonlocal snapshot_count, newest_taken_at, newest_membership
        if current_id is None:
            return

        age_days: Optional[float] = None
        if newest_taken_at is not None:
            age_days = (
                datetime.now(timezone.utc) - _utc_datetime(newest_taken_at)
            ).total_seconds() / 86400.0

        member_count: Optional[int] = None
        if isinstance(newest_membership, list):
            member_count = len(newest_membership)

        perspectives.append(
            PerspectiveSnapshotFootprint(
                perspective_id=current_id,
                name=current_name or "",
                snapshot_count=snapshot_count,
                last_taken_at=_iso8601(newest_taken_at),
                age_days=age_days,
                newest_member_count=member_count,
            )
        )

    for row in rows:
        perspective_id = str(row.perspective_id)
        if perspective_id != current_id:
            append_current()
            current_id = perspective_id
            current_name = str(row.name) if row.name is not None else ""
            snapshot_count = 0
            newest_taken_at = None
            newest_membership = None

        if row.snapshot_id is None:
            continue

        snapshot_count += 1
        total_snapshots += 1
        if newest_taken_at is None:
            newest_taken_at = row.taken_at
            newest_membership = row.membership

    append_current()

    return PerspectiveSnapshotFootprintResponse(
        perspectives=perspectives,
        total_snapshots=total_snapshots,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


def _self_test() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_snapshots (
                    id TEXT PRIMARY KEY,
                    perspective_id TEXT NOT NULL,
                    taken_at DATETIME NOT NULL,
                    membership JSON
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspectives (id, org_id, name, created_at)
                VALUES
                    ('perspective-1', 'org-1', 'First Perspective', '2024-01-01T00:00:00'),
                    ('perspective-2', 'org-1', 'Second Perspective', '2024-01-01T00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_snapshots
                    (id, perspective_id, taken_at, membership)
                VALUES
                    (
                        'snapshot-1',
                        'perspective-1',
                        '2024-01-01T00:00:00',
                        '["member-1", "member-2"]'
                    ),
                    (
                        'snapshot-2',
                        'perspective-1',
                        '2024-01-02T00:00:00',
                        '["member-1", "member-2", "member-3", "member-4"]'
                    ),
                    (
                        'snapshot-3',
                        'perspective-2',
                        '2024-01-03T00:00:00',
                        NULL
                    )
                """
            )
        )

    test_session = TestSession()

    def override_get_session() -> Iterator[Session]:
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/perspectives/snapshots")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_snapshots"] == 3

            by_id = {
                item["perspective_id"]: item
                for item in payload["perspectives"]
            }
            assert by_id["perspective-1"]["snapshot_count"] == 2
            assert by_id["perspective-1"]["newest_member_count"] == 4
            assert by_id["perspective-2"]["snapshot_count"] == 1
            assert by_id["perspective-2"]["newest_member_count"] is None
    finally:
        test_session.close()
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()