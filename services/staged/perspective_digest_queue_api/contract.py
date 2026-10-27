from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


router = APIRouter(prefix="/api", tags=["perspective-digest-queue"])


class PerspectiveDigestItem(BaseModel):
    perspective_id: str
    name: str
    unseen_events: int
    oldest_unseen_at: Optional[str] = None
    distinct_servers_changed: int
    last_snapshot_at: Optional[str] = None


class PerspectiveDigestResponse(BaseModel):
    days: int
    perspectives: List[PerspectiveDigestItem]


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _naive_utc(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _is_seen(value) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "t", "yes", "y"}


def _is_tier_change(change_type, old_tier, new_tier) -> bool:
    ct = "" if change_type is None else str(change_type).lower()
    if "tier" in ct:
        return True
    return old_tier is not None and new_tier is not None and old_tier != new_tier


def get_perspective_digest_queue(days: int, db: Session) -> dict:
    days = int(days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    perspective_rows = db.execute(
        text(
            "SELECT id, name, created_at "
            "FROM perspectives "
            "ORDER BY created_at ASC, id ASC"
        )
    ).mappings().all()

    result = []
    for perspective in perspective_rows:
        perspective_id = perspective["id"]

        event_rows = db.execute(
            text(
                "SELECT server_id, change_type, old_tier, new_tier, seen, created_at "
                "FROM perspective_events "
                "WHERE perspective_id = :perspective_id"
            ),
            {"perspective_id": perspective_id},
        ).mappings().all()

        unseen_events = 0
        oldest_unseen_raw = None
        oldest_unseen_dt = None
        distinct_servers = set()

        for event in event_rows:
            event_dt = _naive_utc(event["created_at"])
            if event_dt is not None and event_dt >= cutoff and event["server_id"] is not None:
                distinct_servers.add(event["server_id"])

            if not _is_seen(event["seen"]) and _is_tier_change(
                event["change_type"], event["old_tier"], event["new_tier"]
            ):
                unseen_events += 1
                if oldest_unseen_dt is None or (
                    event_dt is not None and event_dt < oldest_unseen_dt
                ):
                    oldest_unseen_dt = event_dt
                    oldest_unseen_raw = event["created_at"]

        snapshot_rows = db.execute(
            text(
                "SELECT taken_at FROM perspective_snapshots WHERE perspective_id = :perspective_id"
            ),
            {"perspective_id": perspective_id},
        ).scalars().all()

        last_snapshot_raw = None
        last_snapshot_dt = None
        for taken_at in snapshot_rows:
            taken_dt = _naive_utc(taken_at)
            if last_snapshot_dt is None or (
                taken_dt is not None and taken_dt > last_snapshot_dt
            ):
                last_snapshot_dt = taken_dt
                last_snapshot_raw = taken_at

        result.append(
            {
                "perspective_id": str(perspective_id),
                "name": perspective["name"],
                "unseen_events": unseen_events,
                "oldest_unseen_at": _iso(oldest_unseen_raw),
                "distinct_servers_changed": len(distinct_servers),
                "last_snapshot_at": _iso(last_snapshot_raw),
            }
        )

    return {"days": days, "perspectives": result}


@router.get("/perspectives/digest-queue", response_model=PerspectiveDigestResponse)
def perspective_digest_queue(
    days: int = Query(7, ge=1),
    db: Session = Depends(get_session),
):
    return get_perspective_digest_queue(days, db)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id TEXT PRIMARY KEY,
                    org_id TEXT,
                    name TEXT,
                    description TEXT,
                    facet_filters TEXT,
                    created_by TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
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
        conn.execute(
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO perspectives
                (id, org_id, name, description, facet_filters, created_by, created_at, updated_at)
                VALUES
                (:id, :org_id, :name, :description, :facet_filters, :created_by, :created_at, :updated_at)
                """
            ),
            [
                {
                    "id": "p1",
                    "org_id": "org1",
                    "name": "Perspective One",
                    "description": "first",
                    "facet_filters": "{}",
                    "created_by": "user1",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "p2",
                    "org_id": "org1",
                    "name": "Perspective Two",
                    "description": "second",
                    "facet_filters": "{}",
                    "created_by": "user1",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

        conn.execute(
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
                    "id": "snap1",
                    "perspective_id": "p1",
                    "taken_at": now,
                    "membership": "[]",
                },
                {
                    "id": "snap2",
                    "perspective_id": "p2",
                    "taken_at": now,
                    "membership": "[]",
                },
            ],
        )

        conn.execute(
            text(
                """
                INSERT INTO perspective_events
                (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
                VALUES
                (:id, :perspective_id, :server_id, :change_type, :old_tier, :new_tier, :seen, :created_at)
                """
            ),
            [
                {
                    "id": "e1",
                    "perspective_id": "p1",
                    "server_id": "s1",
                    "change_type": "tier_change",
                    "old_tier": "low",
                    "new_tier": "high",
                    "seen": 0,
                    "created_at": now,
                },
                {
                    "id": "e2",
                    "perspective_id": "p1",
                    "server_id": "s2",
                    "change_type": "tier_change",
                    "old_tier": "medium",
                    "new_tier": "high",
                    "seen": 0,
                    "created_at": now,
                },
                {
                    "id": "e3",
                    "perspective_id": "p2",
                    "server_id": "s3",
                    "change_type": "tier_change",
                    "old_tier": "high",
                    "new_tier": "medium",
                    "seen": 1,
                    "created_at": now,
                },
            ],
        )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/perspectives/digest-queue?days=7")
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["perspectives"]) == 2
        assert data["perspectives"][0]["perspective_id"] == "p1"
        assert data["perspectives"][0]["unseen_events"] == 2
        assert data["perspectives"][1]["unseen_events"] == 0
        assert data["perspectives"][0]["distinct_servers_changed"] == 2
        print("PASS")