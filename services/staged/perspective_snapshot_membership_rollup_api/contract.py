from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


class PerspectiveSnapshotRollupItem(BaseModel):
    perspective_id: Any
    name: Optional[str]
    snapshot_count: int
    latest_taken_at: Optional[str]
    latest_member_count: int
    member_delta: int


class PerspectiveSnapshotRollupResponse(BaseModel):
    total_perspectives: int
    snapshots_taken: int
    perspectives: list[PerspectiveSnapshotRollupItem]
    generated_at: str


def _member_count(membership: Any) -> int:
    if isinstance(membership, (list, tuple, set)):
        return len(membership)
    if isinstance(membership, dict):
        return len(membership)
    return 0


def _timestamp_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _snapshot_sort_key(snapshot: PerspectiveSnapshot) -> str:
    value = snapshot.taken_at
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_snapshot_rollup(session: Session) -> PerspectiveSnapshotRollupResponse:
    perspectives = list(session.execute(select(Perspective)).scalars().all())
    snapshots = list(session.execute(select(PerspectiveSnapshot)).scalars().all())

    snapshots_by_perspective: dict[Any, list[PerspectiveSnapshot]] = {}
    for snapshot in snapshots:
        snapshots_by_perspective.setdefault(snapshot.perspective_id, []).append(snapshot)

    items: list[PerspectiveSnapshotRollupItem] = []
    for perspective in perspectives:
        perspective_snapshots = sorted(
            snapshots_by_perspective.get(perspective.id, []),
            key=_snapshot_sort_key,
        )
        snapshot_count = len(perspective_snapshots)

        if perspective_snapshots:
            latest = perspective_snapshots[-1]
            latest_member_count = _member_count(latest.membership)
            if len(perspective_snapshots) > 1:
                previous = perspective_snapshots[-2]
                member_delta = latest_member_count - _member_count(previous.membership)
            else:
                member_delta = 0
            latest_taken_at = _timestamp_value(latest.taken_at)
        else:
            latest_member_count = 0
            member_delta = 0
            latest_taken_at = None

        items.append(
            PerspectiveSnapshotRollupItem(
                perspective_id=perspective.id,
                name=perspective.name,
                snapshot_count=snapshot_count,
                latest_taken_at=latest_taken_at,
                latest_member_count=latest_member_count,
                member_delta=member_delta,
            )
        )

    return PerspectiveSnapshotRollupResponse(
        total_perspectives=len(perspectives),
        snapshots_taken=len(snapshots),
        perspectives=items,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


router = APIRouter()


@router.get(
    "/perspectives/snapshot-rollup",
    response_model=PerspectiveSnapshotRollupResponse,
)
def get_snapshot_rollup(session: Session = Depends(get_session)) -> PerspectiveSnapshotRollupResponse:
    return build_snapshot_rollup(session)


app = FastAPI(title="perspective_snapshot_membership_rollup_api")
app.include_router(router, prefix="/api")


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE perspectives (
                id VARCHAR PRIMARY KEY,
                org_id VARCHAR,
                name VARCHAR,
                description VARCHAR,
                facet_filters JSON,
                created_by VARCHAR,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE perspective_snapshots (
                id VARCHAR PRIMARY KEY,
                perspective_id VARCHAR,
                taken_at DATETIME,
                membership JSON
            )
            """
        )

        perspective_rows = [
            ("perspective-1", "Changing", "first"),
            ("perspective-2", "Stable One", "second"),
            ("perspective-3", "Stable Two", "third"),
        ]
        for perspective_id, name, description in perspective_rows:
            connection.exec_driver_sql(
                """
                INSERT INTO perspectives
                    (id, org_id, name, description, facet_filters, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    perspective_id,
                    "org-1",
                    name,
                    description,
                    "{}",
                    "user-1",
                    datetime(2024, 1, 1, tzinfo=timezone.utc),
                    datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
            )

        snapshot_rows = [
            ("snapshot-1", "perspective-1", datetime(2024, 1, 1, tzinfo=timezone.utc), '["server-1"]'),
            ("snapshot-2", "perspective-1", datetime(2024, 1, 2, tzinfo=timezone.utc), '["server-1", "server-2"]'),
            ("snapshot-3", "perspective-2", datetime(2024, 1, 1, tzinfo=timezone.utc), '["server-3"]'),
            ("snapshot-4", "perspective-3", datetime(2024, 1, 1, tzinfo=timezone.utc), '["server-4"]'),
        ]
        for snapshot_id, perspective_id, taken_at, membership in snapshot_rows:
            connection.exec_driver_sql(
                """
                INSERT INTO perspective_snapshots
                    (id, perspective_id, taken_at, membership)
                VALUES (?, ?, ?, ?)
                """,
                (snapshot_id, perspective_id, taken_at, membership),
            )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/snapshot-rollup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_perspectives"] == 3
        assert payload["snapshots_taken"] == 4

        by_name = {item["name"]: item for item in payload["perspectives"]}
        assert by_name["Changing"]["member_delta"] == 1
        assert by_name["Stable One"]["member_delta"] == 0
        assert by_name["Stable Two"]["member_delta"] == 0

    print("PASS")


if __name__ == "__main__":
    _run_self_test()