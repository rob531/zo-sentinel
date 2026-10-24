from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


def _timestamp_key(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_perspective_membership_graph(
    db: Session = Depends(get_session),
    org_id: Optional[str] = None,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    perspective_columns = (
        Perspective.id,
        Perspective.org_id,
        Perspective.name,
        Perspective.description,
        Perspective.facet_filters,
        Perspective.created_at,
    )
    perspective_query = select(*perspective_columns)
    if org_id is not None:
        perspective_query = perspective_query.where(Perspective.org_id == org_id)
    if perspective_id is not None:
        perspective_query = perspective_query.where(Perspective.id == perspective_id)
    perspective_query = perspective_query.order_by(Perspective.id)

    perspective_rows = db.execute(perspective_query).all()
    perspective_ids = [row[0] for row in perspective_rows]

    snapshot_rows: List[Tuple[Any, ...]] = []
    event_rows: List[Tuple[Any, ...]] = []
    if perspective_ids:
        snapshot_rows = db.execute(
            select(
                PerspectiveSnapshot.id,
                PerspectiveSnapshot.perspective_id,
                PerspectiveSnapshot.taken_at,
                PerspectiveSnapshot.membership,
            ).where(PerspectiveSnapshot.perspective_id.in_(perspective_ids))
        ).all()

        event_rows = db.execute(
            select(
                PerspectiveEvent.id,
                PerspectiveEvent.perspective_id,
                PerspectiveEvent.server_id,
                PerspectiveEvent.change_type,
                PerspectiveEvent.old_tier,
                PerspectiveEvent.new_tier,
                PerspectiveEvent.seen,
                PerspectiveEvent.created_at,
            ).where(PerspectiveEvent.perspective_id.in_(perspective_ids))
        ).all()

    latest_snapshots: Dict[Any, Tuple[Any, Any, Any]] = {}
    for snapshot_id, owner_id, taken_at, membership in snapshot_rows:
        key = (_timestamp_key(taken_at), str(snapshot_id))
        current = latest_snapshots.get(owner_id)
        if current is None or key > current[0]:
            latest_snapshots[owner_id] = (key, taken_at, membership)

    latest_events: Dict[Tuple[Any, Any], Tuple[Any, Any]] = {}
    unseen_events: Dict[Any, int] = {}
    for (
        event_id,
        owner_id,
        server_id,
        _change_type,
        _old_tier,
        new_tier,
        seen,
        created_at,
    ) in event_rows:
        if not bool(seen):
            unseen_events[owner_id] = unseen_events.get(owner_id, 0) + 1

        event_key = (_timestamp_key(created_at), str(event_id))
        server_key = (owner_id, server_id)
        current = latest_events.get(server_key)
        if current is None or event_key > current[0]:
            latest_events[server_key] = (event_key, new_tier)

    result: List[Dict[str, Any]] = []
    for row in perspective_rows:
        owner_id, owner_org_id, name = row[0], row[1], row[2]
        snapshot = latest_snapshots.get(owner_id)

        members: List[str] = []
        if snapshot is not None:
            membership = snapshot[2]
            if isinstance(membership, str):
                import json

                membership = json.loads(membership)
            if isinstance(membership, list):
                for server_id in membership:
                    if server_id is not None and str(server_id) not in members:
                        members.append(str(server_id))

        tier_counts: Dict[str, int] = {}
        for server_id in members:
            event = latest_events.get((owner_id, server_id))
            tier = event[1] if event is not None else None
            tier_name = str(tier) if tier is not None and str(tier) else "unknown"
            tier_counts[tier_name] = tier_counts.get(tier_name, 0) + 1

        result.append(
            {
                "id": owner_id,
                "name": name,
                "org_id": owner_org_id,
                "server_count": len(members),
                "tier_counts": tier_counts,
                "unseen_events": unseen_events.get(owner_id, 0),
                "last_snapshot_at": _to_iso(snapshot[1]) if snapshot is not None else None,
            }
        )

    return {
        "perspectives": result,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_perspective_graph(
    db: Session = Depends(get_session),
    org_id: Optional[str] = None,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_perspective_membership_graph(
        db=db, org_id=org_id, perspective_id=perspective_id
    )


def build_perspective_membership_graph(
    db: Session = Depends(get_session),
    org_id: Optional[str] = None,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_perspective_membership_graph(
        db=db, org_id=org_id, perspective_id=perspective_id
    )


def get_membership_graph(
    db: Session = Depends(get_session),
    org_id: Optional[str] = None,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    return get_perspective_membership_graph(
        db=db, org_id=org_id, perspective_id=perspective_id
    )


if __name__ == "__main__":
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY,
                    org_id VARCHAR,
                    name VARCHAR,
                    description VARCHAR,
                    facet_filters JSON,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_snapshots (
                    id VARCHAR PRIMARY KEY,
                    perspective_id VARCHAR,
                    taken_at DATETIME,
                    membership JSON
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id VARCHAR PRIMARY KEY,
                    perspective_id VARCHAR,
                    server_id VARCHAR,
                    change_type VARCHAR,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN,
                    created_at DATETIME
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO perspectives
                    (id, org_id, name, description, facet_filters, created_at)
                VALUES
                    (:id, :org_id, :name, :description, :facet_filters, :created_at)
                """
            ),
            [
                {
                    "id": "perspective-1",
                    "org_id": "org-1",
                    "name": "Production",
                    "description": "Production perspective",
                    "facet_filters": json.dumps({"environment": "production"}),
                    "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                },
                {
                    "id": "perspective-2",
                    "org_id": "org-1",
                    "name": "Staging",
                    "description": "Staging perspective",
                    "facet_filters": json.dumps({"environment": "staging"}),
                    "created_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_snapshots
                    (id, perspective_id, taken_at, membership)
                VALUES
                    (:id, :perspective_id, :taken_at, :membership)
                """
            ),
            [
                {
                    "id": "snapshot-1",
                    "perspective_id": "perspective-1",
                    "taken_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
                    "membership": json.dumps(["server-a", "server-b"]),
                },
                {
                    "id": "snapshot-2",
                    "perspective_id": "perspective-2",
                    "taken_at": datetime(2025, 1, 4, tzinfo=timezone.utc),
                    "membership": json.dumps(["server-c"]),
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type,
                     old_tier, new_tier, seen, created_at)
                VALUES
                    (:id, :perspective_id, :server_id, :change_type,
                     :old_tier, :new_tier, :seen, :created_at)
                """
            ),
            [
                {
                    "id": "event-1",
                    "perspective_id": "perspective-1",
                    "server_id": "server-a",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "high",
                    "seen": False,
                    "created_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
                },
                {
                    "id": "event-2",
                    "perspective_id": "perspective-1",
                    "server_id": "server-b",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "medium",
                    "seen": True,
                    "created_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
                },
                {
                    "id": "event-3",
                    "perspective_id": "perspective-2",
                    "server_id": "server-c",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "low",
                    "seen": False,
                    "created_at": datetime(2025, 1, 4, tzinfo=timezone.utc),
                },
            ],
        )

    db = SessionLocal()
    test_app = FastAPI()

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/graph")
    def graph_endpoint(
        session: Session = Depends(get_session),
        org_id: Optional[str] = None,
        perspective_id: Optional[str] = None,
    ):
        return get_perspective_membership_graph(
            db=session, org_id=org_id, perspective_id=perspective_id
        )

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/graph")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["perspectives"]) == 2
        assert {item["id"] for item in payload["perspectives"]} == {
            "perspective-1",
            "perspective-2",
        }
        for item in payload["perspectives"]:
            assert sum(item["tier_counts"].values()) == item["server_count"]
        assert sum(item["unseen_events"] for item in payload["perspectives"]) == 2
        assert payload["generated_at"]

    db.close()
    engine.dispose()
    print("PASS")