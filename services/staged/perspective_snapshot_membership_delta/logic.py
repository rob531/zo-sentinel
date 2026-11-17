from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot


def _membership_server_ids(membership: Any) -> set[str]:
    if not isinstance(membership, list):
        return set()

    server_ids: set[str] = set()
    for member in membership:
        server_id: Any = None
        if isinstance(member, str):
            server_id = member
        elif isinstance(member, dict):
            server_id = member.get("server_id")
            if server_id is None:
                server_id = member.get("id")

        if server_id is not None and str(server_id):
            server_ids.add(str(server_id))

    return server_ids


def get_snapshot_delta(db: Session, perspective_id: str) -> dict[str, Any]:
    snapshots = list(
        db.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(
                PerspectiveSnapshot.taken_at.desc(),
                PerspectiveSnapshot.id.desc(),
            )
            .limit(2)
        ).scalars()
    )

    if len(snapshots) < 2:
        raise HTTPException(
            status_code=404,
            detail="Two perspective snapshots are required to calculate a delta",
        )

    to_snapshot, from_snapshot = snapshots
    from_membership = _membership_server_ids(from_snapshot.membership)
    to_membership = _membership_server_ids(to_snapshot.membership)

    added = sorted(to_membership - from_membership)
    removed = sorted(from_membership - to_membership)

    event_count = db.execute(
        select(func.count())
        .select_from(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
    ).scalar_one()

    return {
        "perspective_id": perspective_id,
        "from_snapshot_id": from_snapshot.id,
        "to_snapshot_id": to_snapshot.id,
        "from_taken_at": _isoformat(from_snapshot.taken_at),
        "to_taken_at": _isoformat(to_snapshot.taken_at),
        "added": added,
        "removed": removed,
        "added_count": len(added),
        "removed_count": len(removed),
        "event_count": int(event_count),
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


compute_snapshot_delta = get_snapshot_delta
snapshot_membership_delta = get_snapshot_delta


def _self_test() -> None:
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    with Session(engine) as session:
        now = datetime.utcnow()
        session.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id="perspective-1",
                    taken_at=now - timedelta(minutes=10),
                    membership=["server-1", "server-2"],
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id="perspective-1",
                    taken_at=now,
                    membership=[{"server_id": "server-2"}, "server-3"],
                ),
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-3",
                    change_type="added",
                    old_tier=None,
                    new_tier="active",
                    seen=False,
                    created_at=now,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="removed",
                    old_tier="active",
                    new_tier=None,
                    seen=True,
                    created_at=now,
                ),
            ]
        )
        session.commit()

        that_app = FastAPI()

        @that_app.get("/api/perspectives/{perspective_id}/snapshot-delta")
        def snapshot_delta_endpoint(
            perspective_id: str,
            db: Session = Depends(get_session),
        ) -> dict[str, Any]:
            return get_snapshot_delta(db, perspective_id)

        def override_get_session() -> Iterable[Session]:
            yield session

        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get(
                "/api/perspectives/perspective-1/snapshot-delta"
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["perspective_id"] == "perspective-1"
            assert payload["from_snapshot_id"] == 1
            assert payload["to_snapshot_id"] == 2
            assert payload["added"] == ["server-3"]
            assert payload["removed"] == ["server-1"]
            assert payload["added_count"] == 1
            assert payload["removed_count"] == 1
            assert payload["event_count"] == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()