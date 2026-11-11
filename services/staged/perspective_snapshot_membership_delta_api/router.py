from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot
from .logic import get_membership_delta


router = APIRouter(prefix="/api", tags=["perspective-snapshot-membership-delta"])


class MembershipDeltaResponse(BaseModel):
    perspective_id: str
    from_snapshot_id: int | None
    to_snapshot_id: int | None
    added: list[str]
    removed: list[str]
    added_count: int
    removed_count: int
    event_counts: dict[str, int]


def _membership_ids(membership: Any) -> list[str]:
    if isinstance(membership, dict):
        membership = membership.get("server_ids", [])
    if not isinstance(membership, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for server_id in membership:
        if server_id is None:
            continue
        value = str(server_id)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


async def _call_logic(
    session: Session,
    perspective_id: str,
    from_snapshot_id: int | None,
    to_snapshot_id: int | None,
) -> Any:
    try:
        result = get_membership_delta(
            session=session,
            perspective_id=perspective_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
        )
    except TypeError:
        result = get_membership_delta(
            session,
            perspective_id,
            from_snapshot_id,
            to_snapshot_id,
        )

    if inspect.isawaitable(result):
        return await result
    return result


@router.get(
    "/perspectives/{perspective_id}/membership-delta",
    response_model=MembershipDeltaResponse,
)
async def membership_delta(
    perspective_id: str,
    from_snapshot_id: int | None = None,
    to_snapshot_id: int | None = None,
    session: Session = Depends(get_session),
) -> Any:
    return await _call_logic(
        session=session,
        perspective_id=perspective_id,
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
    )


def _self_test() -> None:
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
                CREATE TABLE perspective_snapshots (
                    id INTEGER PRIMARY KEY,
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
                CREATE TABLE perspective_events (
                    id INTEGER PRIMARY KEY,
                    perspective_id VARCHAR NOT NULL,
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
                INSERT INTO perspective_snapshots
                    (id, perspective_id, taken_at, membership)
                VALUES
                    (1, 'p1', '2024-01-01 00:00:00', '[]'),
                    (2, 'p1', '2024-01-02 00:00:00', '["a", "b"]'),
                    (3, 'p1', '2024-01-03 00:00:00', '["b", "c"]')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type, created_at)
                VALUES
                    (1, 'p1', 'c', 'added', '2024-01-03 00:00:00'),
                    (2, 'p1', 'a', 'removed', '2024-01-03 00:00:00')
                """
            )
        )

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/p1/membership-delta")
        assert response.status_code == 200
        payload = response.json()
        assert payload["added"] == ["c"]
        assert payload["removed"] == ["a"]
        assert payload["added_count"] == 1
        assert payload["removed_count"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()