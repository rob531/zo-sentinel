from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot
from .logic import Perspective as _LogicPerspective
from .logic import PerspectiveSnapshot as _LogicPerspectiveSnapshot

router = APIRouter(prefix="/api")


def _membership_size(membership: Any) -> int:
    return len(membership) if isinstance(membership, list) else 0


@router.get("/perspectives/membership-drift")
def perspective_membership_drift(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    perspectives = (
        session.query(Perspective)
        .order_by(Perspective.id)
        .all()
    )
    snapshots = (
        session.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.taken_at >= cutoff)
        .order_by(
            PerspectiveSnapshot.perspective_id,
            PerspectiveSnapshot.taken_at,
            PerspectiveSnapshot.id,
        )
        .all()
    )

    snapshots_by_perspective: dict[str, list[PerspectiveSnapshot]] = {}
    for snapshot in snapshots:
        snapshots_by_perspective.setdefault(snapshot.perspective_id, []).append(snapshot)

    results = []
    for perspective in perspectives:
        perspective_snapshots = snapshots_by_perspective.get(perspective.id, [])
        sizes = [_membership_size(snapshot.membership) for snapshot in perspective_snapshots]
        net_change = sizes[-1] - sizes[0] if sizes else 0

        results.append(
            {
                "perspective_id": perspective.id,
                "name": perspective.name,
                "snapshot_count": len(perspective_snapshots),
                "membership_size_series": [
                    {
                        "taken_at": snapshot.taken_at.isoformat(),
                        "size": _membership_size(snapshot.membership),
                    }
                    for snapshot in perspective_snapshots
                ],
                "net_change": net_change,
                "abs_change": abs(net_change),
            }
        )

    return {"days": days, "perspectives": results}


if __name__ == "__main__":
    import json
    from datetime import datetime as datetime_type

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY NOT NULL,
                    name VARCHAR,
                    created_by VARCHAR NOT NULL,
                    facet_filters JSON NOT NULL,
                    description VARCHAR,
                    created_at DATETIME,
                    updated_at DATETIME,
                    org_id VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_snapshots (
                    id VARCHAR PRIMARY KEY NOT NULL,
                    perspective_id VARCHAR NOT NULL,
                    taken_at DATETIME NOT NULL,
                    membership JSON
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO perspectives (id, name, created_by, facet_filters)
                VALUES (:id, :name, :created_by, :facet_filters)
                """
            ),
            [
                {
                    "id": "perspective-1",
                    "name": "First Perspective",
                    "created_by": "contract",
                    "facet_filters": json.dumps([]),
                },
                {
                    "id": "perspective-2",
                    "name": "Second Perspective",
                    "created_by": "contract",
                    "facet_filters": json.dumps([]),
                },
            ],
        )

        now = datetime_type.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership)
                VALUES (:id, :perspective_id, :taken_at, :membership)
                """
            ),
            [
                {
                    "id": "snapshot-1",
                    "perspective_id": "perspective-1",
                    "taken_at": now - timedelta(days=2),
                    "membership": json.dumps(["a", "b", "c"]),
                },
                {
                    "id": "snapshot-2",
                    "perspective_id": "perspective-1",
                    "taken_at": now - timedelta(days=1),
                    "membership": json.dumps(["a", "b", "c", "d", "e"]),
                },
                {
                    "id": "snapshot-3",
                    "perspective_id": "perspective-2",
                    "taken_at": now - timedelta(days=1),
                    "membership": json.dumps(["a", "b"]),
                },
            ],
        )

    def override_get_session():
        database_session = TestingSessionLocal()
        try:
            yield database_session
        finally:
            database_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/perspectives/membership-drift",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["perspectives"]) == 2

    first = next(
        item for item in payload["perspectives"] if item["perspective_id"] == "perspective-1"
    )
    second = next(
        item for item in payload["perspectives"] if item["perspective_id"] == "perspective-2"
    )
    assert first["snapshot_count"] == 2
    assert first["net_change"] == 2
    assert first["abs_change"] == 2
    assert second["snapshot_count"] == 1
    assert second["net_change"] == 0
    assert second["abs_change"] == 0

    print("PASS")