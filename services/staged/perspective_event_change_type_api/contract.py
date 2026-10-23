from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


class ChangeTypeVolume(BaseModel):
    change_type: str
    count: int
    distinct_servers: int
    unseen_count: int
    first_seen_at: str
    last_seen_at: str


class ChangeTypeVolumeResponse(BaseModel):
    total_events: int
    by_change_type: List[ChangeTypeVolume]
    generated_at: str


def _isoformat(value: object) -> str:
    if isinstance(value, datetime):
        result = value.isoformat()
        if value.tzinfo is None:
            result = f"{result}+00:00"
        return result
    return str(value)


def get_change_type_rollup(
    perspective_id: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=0),
    session: Session = Depends(get_session),
) -> ChangeTypeVolumeResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(
            PerspectiveEvent.change_type.label("change_type"),
            func.count(PerspectiveEvent.id).label("event_count"),
            func.count(func.distinct(PerspectiveEvent.server_id)).label(
                "distinct_servers"
            ),
            func.sum(
                case((PerspectiveEvent.seen.is_(True), 1), else_=0)
            ).label("seen_count"),
            func.min(PerspectiveEvent.created_at).label("first_seen_at"),
            func.max(PerspectiveEvent.created_at).label("last_seen_at"),
        )
        .join(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .where(PerspectiveEvent.created_at >= cutoff)
        .group_by(PerspectiveEvent.change_type)
        .order_by(PerspectiveEvent.change_type)
    )

    if perspective_id is not None:
        statement = statement.where(PerspectiveEvent.perspective_id == perspective_id)

    rows = session.execute(statement).all()
    by_change_type = [
        ChangeTypeVolume(
            change_type=str(row.change_type),
            count=int(row.event_count),
            distinct_servers=int(row.distinct_servers),
            unseen_count=int(row.event_count - (row.seen_count or 0)),
            first_seen_at=_isoformat(row.first_seen_at),
            last_seen_at=_isoformat(row.last_seen_at),
        )
        for row in rows
    ]

    return ChangeTypeVolumeResponse(
        total_events=sum(item.count for item in by_change_type),
        by_change_type=by_change_type,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


router = APIRouter(prefix="/api")
router.get(
    "/perspectives/events/change-types",
    response_model=ChangeTypeVolumeResponse,
)(get_change_type_rollup)

app = FastAPI()
app.include_router(router)


def main() -> int:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with Session(engine) as session:
        session.execute(
            text(
                """
                CREATE TABLE perspectives (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE perspective_events (
                    id VARCHAR PRIMARY KEY,
                    perspective_id VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    change_type VARCHAR NOT NULL,
                    old_tier VARCHAR,
                    new_tier VARCHAR,
                    seen BOOLEAN,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        session.execute(
            text("INSERT INTO perspectives (id, name) VALUES (:id, :name)"),
            [
                {"id": "p1", "name": "Primary Perspective"},
                {"id": "p2", "name": "Filtered Perspective"},
            ],
        )

        now = datetime.now(timezone.utc)
        session.execute(
            text(
                """
                INSERT INTO perspective_events (
                    id, perspective_id, server_id, change_type,
                    old_tier, new_tier, seen, created_at
                ) VALUES (
                    :id, :perspective_id, :server_id, :change_type,
                    :old_tier, :new_tier, :seen, :created_at
                )
                """
            ),
            [
                {
                    "id": "e1",
                    "perspective_id": "p1",
                    "server_id": "server-a",
                    "change_type": "tier_up",
                    "old_tier": "low",
                    "new_tier": "medium",
                    "seen": True,
                    "created_at": now - timedelta(hours=3),
                },
                {
                    "id": "e2",
                    "perspective_id": "p1",
                    "server_id": "server-b",
                    "change_type": "tier_down",
                    "old_tier": "medium",
                    "new_tier": "low",
                    "seen": False,
                    "created_at": now - timedelta(hours=2),
                },
                {
                    "id": "e3",
                    "perspective_id": "p2",
                    "server_id": "server-c",
                    "change_type": "tier_up",
                    "old_tier": "low",
                    "new_tier": "high",
                    "seen": False,
                    "created_at": now - timedelta(hours=1),
                },
            ],
        )
        session.commit()

        def override_session():
            yield session

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_session

        with TestClient(that_app) as client:
            response = client.get("/api/perspectives/events/change-types")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_events"] == 3, payload
            assert len(payload["by_change_type"]) == 2, payload

            counts = {
                item["change_type"]: item["count"]
                for item in payload["by_change_type"]
            }
            assert counts["tier_up"] == 2, payload
            assert any(item["unseen_count"] == 1 for item in payload["by_change_type"]), payload

            filtered = client.get(
                "/api/perspectives/events/change-types",
                params={"perspective_id": "p2"},
            )
            assert filtered.status_code == 200, filtered.text
            filtered_payload = filtered.json()
            assert filtered_payload["total_events"] == 1, filtered_payload
            assert len(filtered_payload["by_change_type"]) == 1, filtered_payload
            assert filtered_payload["by_change_type"][0]["change_type"] == "tier_up"
            assert filtered_payload["by_change_type"][0]["unseen_count"] == 1

    engine.dispose()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())