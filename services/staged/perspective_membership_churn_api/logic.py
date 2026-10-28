from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _member_count(membership: Any) -> int:
    if isinstance(membership, list):
        return len(membership)
    if isinstance(membership, (str, bytes, bytearray)):
        try:
            decoded = json.loads(membership)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0
        return len(decoded) if isinstance(decoded, list) else 0
    return 0


def get_membership_churn(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if isinstance(perspective_id, Session) and isinstance(session, int):
        perspective_id, session = session, perspective_id

    perspective = session.get(Perspective, perspective_id)
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    snapshots = (
        session.execute(
            select(func.count(PerspectiveSnapshot.id)).where(
                PerspectiveSnapshot.perspective_id == perspective_id
            )
        )
        .scalar_one()
        or 0
    )

    latest_snapshot = session.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(
            case((PerspectiveSnapshot.taken_at.is_(None), 1), else_=0).asc(),
            PerspectiveSnapshot.taken_at.desc(),
            PerspectiveSnapshot.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()

    change_type_rows = session.execute(
        select(PerspectiveEvent.change_type, func.count(PerspectiveEvent.id))
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .group_by(PerspectiveEvent.change_type)
    ).all()
    by_change_type = {
        str(change_type): count
        for change_type, count in change_type_rows
        if change_type is not None
    }

    unseen_events = (
        session.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        )
        .scalar_one()
        or 0
    )

    transition_rows = session.execute(
        select(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id),
        )
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.old_tier.is_not(None),
            PerspectiveEvent.new_tier.is_not(None),
        )
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .order_by(
            func.count(PerspectiveEvent.id).desc(),
            PerspectiveEvent.old_tier.asc(),
            PerspectiveEvent.new_tier.asc(),
        )
    ).all()

    return {
        "name": perspective.name,
        "snapshots": int(snapshots),
        "latest_taken_at": (
            latest_snapshot.taken_at if latest_snapshot is not None else None
        ),
        "latest_member_count": (
            _member_count(latest_snapshot.membership)
            if latest_snapshot is not None
            else 0
        ),
        "by_change_type": by_change_type,
        "unseen_events": int(unseen_events),
        "tier_transitions": [
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": int(count),
            }
            for old_tier, new_tier, count in transition_rows
        ],
    }


get_perspective_membership_churn = get_membership_churn
compute_membership_churn = get_membership_churn


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    Perspective.__table__.create(bind=test_engine)
    PerspectiveSnapshot.__table__.create(bind=test_engine)
    PerspectiveEvent.__table__.create(bind=test_engine)

    with TestSession() as session:
        session.add(
            Perspective(
                id=1,
                org_id=1,
                name="Membership churn perspective",
                description="Self-test perspective",
                facet_filters={},
                created_by="self-test",
                created_at=datetime(2024, 1, 1, 0, 0, 0),
                updated_at=datetime(2024, 1, 1, 0, 0, 0),
            )
        )
        session.add_all(
            [
                PerspectiveSnapshot(
                    id=1,
                    perspective_id=1,
                    taken_at=datetime(2024, 1, 1, 12, 0, 0),
                    membership=["server-1", "server-2"],
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id=1,
                    taken_at=datetime(2024, 1, 2, 12, 0, 0),
                    membership=["server-1", "server-2", "server-3"],
                ),
                PerspectiveEvent(
                    id=1,
                    perspective_id=1,
                    server_id="server-1",
                    change_type="tier_changed",
                    old_tier="standard",
                    new_tier="premium",
                    seen=True,
                    created_at=datetime(2024, 1, 2, 12, 1, 0),
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=1,
                    server_id="server-2",
                    change_type="tier_changed",
                    old_tier="standard",
                    new_tier="premium",
                    seen=True,
                    created_at=datetime(2024, 1, 2, 12, 2, 0),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id=1,
                    server_id="server-3",
                    change_type="added",
                    old_tier=None,
                    new_tier="standard",
                    seen=False,
                    created_at=datetime(2024, 1, 2, 12, 3, 0),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/perspectives/{perspective_id}/membership-churn")
    def membership_churn(
        perspective_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_membership_churn(perspective_id, session)

    test_app.dependency_overrides[get_session] = lambda: TestSession()
    response = TestClient(test_app).get("/api/perspectives/1/membership-churn")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshots"] == 2
    assert payload["latest_member_count"] == 3
    assert {
        "old_tier": "standard",
        "new_tier": "premium",
        "count": 2,
    } in payload["tier_transitions"]
    assert payload["unseen_events"] == 1
    print("PASS")