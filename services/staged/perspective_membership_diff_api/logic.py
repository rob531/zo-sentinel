from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot


router = APIRouter()


class PerspectiveMembershipDiffCounts(BaseModel):
    added: int
    removed: int
    unchanged: int


class PerspectiveMembershipDiffResponse(BaseModel):
    perspective_id: str
    sufficient_snapshots: bool
    from_taken_at: Optional[str]
    to_taken_at: Optional[str]
    added: list[str]
    removed: list[str]
    unchanged: list[str]
    counts: PerspectiveMembershipDiffCounts


def _normalise_membership(value: Any) -> list[str]:
    """Return a stable, de-duplicated list of server IDs from either JSON shape."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if isinstance(value, dict):
        value = value.get("server_ids", [])

    if not isinstance(value, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        server_id = item if isinstance(item, str) else str(item)
        if server_id not in seen:
            seen.add(server_id)
            result.append(server_id)
    return result


def _format_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def get_perspective_membership_diff(
    perspective_id: str,
    session: AsyncSession = Depends(get_session),
) -> PerspectiveMembershipDiffResponse:
    result = await session.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .limit(2)
    )
    snapshots = list(result.scalars().all())

    if len(snapshots) < 2:
        return PerspectiveMembershipDiffResponse(
            perspective_id=perspective_id,
            sufficient_snapshots=False,
            from_taken_at=None,
            to_taken_at=None,
            added=[],
            removed=[],
            unchanged=[],
            counts=PerspectiveMembershipDiffCounts(
                added=0,
                removed=0,
                unchanged=0,
            ),
        )

    older_snapshot, newer_snapshot = snapshots[1], snapshots[0]
    older_members = _normalise_membership(older_snapshot.membership)
    newer_members = _normalise_membership(newer_snapshot.membership)

    older_set = set(older_members)
    newer_set = set(newer_members)

    added = [server_id for server_id in newer_members if server_id not in older_set]
    removed = [server_id for server_id in older_members if server_id not in newer_set]
    unchanged = [server_id for server_id in newer_members if server_id in older_set]

    return PerspectiveMembershipDiffResponse(
        perspective_id=perspective_id,
        sufficient_snapshots=True,
        from_taken_at=_format_timestamp(older_snapshot.taken_at),
        to_taken_at=_format_timestamp(newer_snapshot.taken_at),
        added=added,
        removed=removed,
        unchanged=unchanged,
        counts=PerspectiveMembershipDiffCounts(
            added=len(added),
            removed=len(removed),
            unchanged=len(unchanged),
        ),
    )


router.add_api_route(
    "/perspectives/{perspective_id}/diff",
    get_perspective_membership_diff,
    methods=["GET"],
    response_model=PerspectiveMembershipDiffResponse,
)


if __name__ == "__main__":
    import asyncio

    import httpx
    from fastapi import FastAPI
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.models import Perspective

    async def self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Perspective.__table__.create)
            await connection.run_sync(PerspectiveSnapshot.__table__.create)

        async with session_factory() as session:
            await session.execute(
                insert(PerspectiveSnapshot).values(
                    perspective_id="p-test",
                    taken_at=datetime(2024, 1, 1, 0, 0, 0),
                    membership=["s1", "s2"],
                )
            )
            await session.execute(
                insert(PerspectiveSnapshot).values(
                    perspective_id="p-test",
                    taken_at=datetime(2024, 1, 2, 0, 0, 0),
                    membership=["s2", "s3"],
                )
            )
            await session.commit()

        async def override_get_session() -> AsyncIterator[AsyncSession]:
            async with session_factory() as session:
                yield session

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_get_session
        test_app.include_router(router, prefix="/api")

        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/api/perspectives/p-test/diff")
            assert response.status_code == 200
            payload = response.json()
            assert payload["added"] == ["s3"]
            assert payload["removed"] == ["s1"]
            assert payload["unchanged"] == ["s2"]
            assert payload["sufficient_snapshots"] is True
            assert payload["counts"]["added"] == 1
            assert payload["counts"]["removed"] == 1
            assert payload["counts"]["unchanged"] == 1
            assert (
                payload["counts"]["added"]
                + payload["counts"]["removed"]
                + payload["counts"]["unchanged"]
                == len(payload["added"])
                + len(payload["removed"])
                + len(payload["unchanged"])
            )

        await engine.dispose()

    asyncio.run(self_test())
    print("PASS")