from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api")


class SnapshotRollup(BaseModel):
    taken_at: str
    member_count: int
    tiers: Dict[str, int]


class RecentEvent(BaseModel):
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    created_at: str


class EventRollup(BaseModel):
    total: int
    unseen: int
    by_change_type: Dict[str, int]
    recent: List[RecentEvent]


class MembershipTotals(BaseModel):
    member_count: int
    tier_counts: Dict[str, int]


class MembershipRollupResponse(BaseModel):
    perspective_id: str
    name: Optional[str]
    snapshots: List[SnapshotRollup]
    events: EventRollup
    totals: MembershipTotals


def _isoformat(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _decode_membership(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return value


def _membership_counts(value) -> tuple:
    entries = _decode_membership(value)
    member_count = 0
    tiers: Dict[str, int] = {}

    for entry in entries:
        if isinstance(entry, str):
            member_count += 1
            continue
        if not isinstance(entry, dict) or "server_id" not in entry:
            continue

        member_count += 1
        tier = entry.get("risk_tier")
        if tier is None:
            tier = entry.get("tier")
        if tier is not None and str(tier):
            tier_name = str(tier)
            tiers[tier_name] = tiers.get(tier_name, 0) + 1

    return member_count, tiers


@router.get(
    "/perspectives/{perspective_id}/membership-rollup",
    response_model=MembershipRollupResponse,
)
def get_membership_rollup(
    perspective_id: str,
    session: Session = Depends(get_session),
) -> MembershipRollupResponse:
    perspective = session.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()

    snapshots = list(
        session.execute(
            select(PerspectiveSnapshot)
            .where(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(PerspectiveSnapshot.taken_at.asc(), PerspectiveSnapshot.id.asc())
        ).scalars()
    )

    events = list(
        session.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id == perspective_id)
            .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
        ).scalars()
    )

    snapshot_rollups = []
    latest_tiers: Dict[str, int] = {}
    latest_member_count = 0

    for snapshot in snapshots:
        member_count, tiers = _membership_counts(snapshot.membership)
        snapshot_rollups.append(
            SnapshotRollup(
                taken_at=_isoformat(snapshot.taken_at),
                member_count=member_count,
                tiers=tiers,
            )
        )
        latest_member_count = member_count
        latest_tiers = tiers

    by_change_type: Dict[str, int] = {}
    unseen = 0
    for event in events:
        change_type = str(event.change_type)
        by_change_type[change_type] = by_change_type.get(change_type, 0) + 1
        if not bool(event.seen):
            unseen += 1

    recent_events = [
        RecentEvent(
            server_id=str(event.server_id),
            change_type=str(event.change_type),
            old_tier=event.old_tier,
            new_tier=event.new_tier,
            created_at=_isoformat(event.created_at),
        )
        for event in events[:10]
    ]

    return MembershipRollupResponse(
        perspective_id=perspective_id,
        name=perspective.name if perspective is not None else None,
        snapshots=snapshot_rollups,
        events=EventRollup(
            total=len(events),
            unseen=unseen,
            by_change_type=by_change_type,
            recent=recent_events,
        ),
        totals=MembershipTotals(
            member_count=latest_member_count,
            tier_counts=latest_tiers,
        ),
    )


app = FastAPI(title="Perspective Membership Rollup API")
app.include_router(router)


def run_contract_test() -> None:
    import json
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id TEXT PRIMARY KEY,
                    created_at DATETIME,
                    created_by TEXT,
                    description TEXT,
                    facet_filters TEXT,
                    name TEXT,
                    org_id TEXT,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_snapshots (
                    id TEXT PRIMARY KEY,
                    perspective_id TEXT,
                    taken_at DATETIME,
                    membership TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id TEXT PRIMARY KEY,
                    perspective_id TEXT,
                    server_id TEXT,
                    change_type TEXT,
                    old_tier TEXT,
                    new_tier TEXT,
                    seen INTEGER,
                    created_at DATETIME
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO perspectives
                    (id, created_at, created_by, description, facet_filters,
                     name, org_id, updated_at)
                VALUES
                    (:id, :created_at, :created_by, :description, :facet_filters,
                     :name, :org_id, :updated_at)
                """
            ),
            {
                "id": "perspective-1",
                "created_at": datetime(2024, 1, 1, 12, 0, 0),
                "created_by": "user-1",
                "description": "Contract test perspective",
                "facet_filters": json.dumps({}),
                "name": "Primary perspective",
                "org_id": "org-1",
                "updated_at": datetime(2024, 1, 3, 12, 0, 0),
            },
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
            {
                "id": "snapshot-1",
                "perspective_id": "perspective-1",
                "taken_at": datetime(2024, 1, 1, 12, 0, 0),
                "membership": json.dumps(["s1", "s2"]),
            },
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
            {
                "id": "snapshot-2",
                "perspective_id": "perspective-1",
                "taken_at": datetime(2024, 1, 2, 12, 0, 0),
                "membership": json.dumps(
                    [{"server_id": "s1", "risk_tier": "trusted"}]
                ),
            },
        )

        event_rows = [
            (
                "event-1",
                "s1",
                "trusted",
                "standard",
                1,
                datetime(2024, 1, 3, 12, 0, 0),
            ),
            (
                "event-2",
                "s2",
                "standard",
                "restricted",
                0,
                datetime(2024, 1, 2, 12, 0, 0),
            ),
            (
                "event-3",
                "s3",
                None,
                "trusted",
                1,
                datetime(2024, 1, 1, 12, 0, 0),
            ),
        ]
        for event_id, server_id, old_tier, new_tier, seen, created_at in event_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO perspective_events
                        (id, perspective_id, server_id, change_type, old_tier,
                         new_tier, seen, created_at)
                    VALUES
                        (:id, :perspective_id, :server_id, :change_type, :old_tier,
                         :new_tier, :seen, :created_at)
                    """
                ),
                {
                    "id": event_id,
                    "perspective_id": "perspective-1",
                    "server_id": server_id,
                    "change_type": "tier_shift",
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "seen": seen,
                    "created_at": created_at,
                },
            )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/perspectives/perspective-1/membership-rollup"
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["perspective_id"] == "perspective-1"
        assert payload["name"] == "Primary perspective"
        assert len(payload["snapshots"]) == 2
        assert payload["snapshots"][0]["member_count"] == 2
        assert payload["snapshots"][0]["tiers"] == {}
        assert payload["snapshots"][1]["member_count"] == 1
        assert payload["snapshots"][1]["tiers"] == {"trusted": 1}
        assert payload["events"]["total"] == 3
        assert payload["events"]["unseen"] == 1
        assert len(payload["events"]["by_change_type"]) == 1
        assert payload["events"]["by_change_type"]["tier_shift"] == 3
        assert len(payload["events"]["recent"]) == 3
        assert payload["totals"]["member_count"] == 1
        assert payload["totals"]["tier_counts"] == {"trusted": 1}

    print("PASS")


if __name__ == "__main__":
    run_contract_test()