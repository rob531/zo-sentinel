from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent


class UnseenEvent(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    server_name: str | None
    change_type: str
    old_tier: str | None
    new_tier: str | None
    seen: bool
    created_at: str
    current_risk_tier: str | None


class UnseenEventQueueResponse(BaseModel):
    perspective_id: str | None
    limit: int
    total: int
    events: list[UnseenEvent]
    by_change_type: dict[str, int]


def _isoformat(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_unseen_events(
    session: Session,
    limit: int = 50,
    perspective_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    filters = [PerspectiveEvent.seen.is_(False)]
    if perspective_id is not None:
        filters.append(PerspectiveEvent.perspective_id == perspective_id)

    events_statement = (
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.server_id,
            McpServerRegistry.name.label("server_name"),
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
            McpServerRegistry.risk_tier.label("current_risk_tier"),
        )
        .select_from(PerspectiveEvent)
        .outerjoin(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.server_id,
        )
        .where(*filters)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(limit)
    )

    total_statement = (
        select(func.count(PerspectiveEvent.id))
        .select_from(PerspectiveEvent)
        .where(*filters)
    )

    counts_statement = (
        select(
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id).label("count"),
        )
        .select_from(PerspectiveEvent)
        .where(*filters)
        .group_by(PerspectiveEvent.change_type)
    )

    event_rows = session.execute(events_statement).mappings().all()
    total = int(session.execute(total_statement).scalar_one())
    count_rows = session.execute(counts_statement).all()

    events = [
        {
            "id": int(row["id"]),
            "perspective_id": str(row["perspective_id"]),
            "server_id": str(row["server_id"]),
            "server_name": (
                str(row["server_name"]) if row["server_name"] is not None else None
            ),
            "change_type": str(row["change_type"]),
            "old_tier": str(row["old_tier"]) if row["old_tier"] is not None else None,
            "new_tier": str(row["new_tier"]) if row["new_tier"] is not None else None,
            "seen": bool(row["seen"]),
            "created_at": _isoformat(row["created_at"]),
            "current_risk_tier": (
                str(row["current_risk_tier"])
                if row["current_risk_tier"] is not None
                else None
            ),
        }
        for row in event_rows
    ]
    by_change_type = {
        str(change_type): int(count) for change_type, count in count_rows
    }

    return events, total, by_change_type


router = APIRouter()


@router.get(
    "/perspectives/unseen-events",
    response_model=UnseenEventQueueResponse,
)
def list_unseen_events(
    limit: int = Query(default=50, ge=1),
    perspective_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    events, total, by_change_type = get_unseen_events(
        session=session,
        limit=limit,
        perspective_id=perspective_id,
    )
    return {
        "perspective_id": perspective_id,
        "limit": limit,
        "total": total,
        "events": events,
        "by_change_type": by_change_type,
    }


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    McpServerRegistry.metadata.create_all(test_engine)

    with TestSession() as session:
        session.add(
            Perspective(
                id="perspective-test-1",
                name="Test Perspective",
                created_by="test@example.com",
                description="Contract test perspective",
                facet_filters={},
            )
        )
        session.add(
            McpServerRegistry(
                server_id="server-test-1",
                name="Contract Test Server",
                risk_tier="high",
            )
        )
        session.add_all(
            [
                PerspectiveEvent(
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_raise",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_drop",
                    old_tier="high",
                    new_tier="low",
                    seen=False,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_raise",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        response = client.get(
            "/api/perspectives/unseen-events",
            params={"limit": 10, "perspective_id": "perspective-test-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert len(payload["events"]) == 2
        assert all(event["seen"] is False for event in payload["events"])
        assert payload["by_change_type"] == {"tier_raise": 1, "tier_drop": 1}
        assert payload["events"][0]["server_name"] == "Contract Test Server"
        assert payload["events"][0]["current_risk_tier"] == "high"
        assert all(event["id"] != 3 for event in payload["events"])

    print("PASS")