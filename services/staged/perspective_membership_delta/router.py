"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the membership-delta API and delegates data access to
`logic.py` through the real application database session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

from .logic import get_membership_delta

router = APIRouter(prefix="/api", tags=["perspective-membership-delta"])


class MembershipDeltaSeriesPoint(BaseModel):
    date: str
    added: int
    removed: int
    changed: int


class MembershipDeltaTotals(BaseModel):
    servers_added: int
    servers_removed: int
    tier_changes: int


class MembershipDeltaChange(BaseModel):
    server_id: str | int
    old_tier: str | None = None
    new_tier: str | None = None


class MembershipDeltaResponse(BaseModel):
    perspective_id: str | int
    days: int
    series: list[MembershipDeltaSeriesPoint]
    totals: MembershipDeltaTotals
    changes: list[MembershipDeltaChange]


@router.get(
    "/perspectives/{perspective_id}/membership-delta",
    response_model=MembershipDeltaResponse,
)
def get_perspective_membership_delta(
    perspective_id: int,
    days: int = Query(14, ge=1, le=365),
    db: Session = Depends(get_session),
) -> MembershipDeltaResponse:
    result: dict[str, Any] = get_membership_delta(db, perspective_id, days)
    return MembershipDeltaResponse(
        perspective_id=result.get("perspective_id", perspective_id),
        days=result.get("days", days),
        series=result.get("series", []),
        totals=result.get("totals", {}),
        changes=result.get("changes", []),
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Perspective.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        db.add(
            Perspective(
                id=1,
                name="Test perspective",
                description="Test perspective",
                created_by="self-test",
                facet_filters={},
            )
        )
        db.add(
            PerspectiveSnapshot(
                id=1,
                perspective_id=1,
                taken_at=now - timedelta(days=2),
                membership={"server-1": "low", "server-2": "standard"},
            )
        )
        db.add(
            PerspectiveSnapshot(
                id=2,
                perspective_id=1,
                taken_at=now - timedelta(days=1),
                membership={"server-1": "high", "server-3": "standard"},
            )
        )
        db.add(
            PerspectiveEvent(
                id=1,
                perspective_id=1,
                server_id="server-1",
                change_type="tier_changed",
                old_tier="low",
                new_tier="high",
                seen=False,
                created_at=now - timedelta(days=1),
            )
        )
        db.add(
            PerspectiveEvent(
                id=2,
                perspective_id=1,
                server_id="server-3",
                change_type="added",
                old_tier=None,
                new_tier="standard",
                seen=False,
                created_at=now - timedelta(days=1),
            )
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = (
        lambda: TestingSession()
    )

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/1/membership-delta?days=14")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["servers_added"] == 1, payload
        assert payload["totals"]["servers_removed"] == 1, payload
        assert payload["totals"]["tier_changes"] == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()