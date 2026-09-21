# services/staged/verdict_watchlist/logic.py
import datetime
from typing import List, Dict, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select, delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpWatchlist,
    McpLlmAxisScore,
)

# trust gating override – returns a verdict string for a server
from services.staged.trust_gating_override import trust_gate


# --------------------------------------------------------------------------- #
# Pydantic response models (used by routers)
# --------------------------------------------------------------------------- #
from pydantic import BaseModel


class WatchlistItem(BaseModel):
    server_id: int
    name: str
    risk_tier: str
    verdict: str
    last_assessed: datetime.datetime


class WatchlistResponse(BaseModel):
    items: List[WatchlistItem]


class AxisScore(BaseModel):
    axis: str
    score: float


class RiskDetailResponse(BaseModel):
    server_id: int
    axes: List[AxisScore]
    verdict: str


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #


async def get_watchlist(
    current_user=Depends(),
    db: AsyncSession = Depends(get_session),
) -> WatchlistResponse:
    """Return the watchlist for the caller's organisation."""
    org_id = getattr(current_user, "org_id", None)
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User organisation not found",
        )

    stmt = (
        select(
            McpWatchlist.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
        .join(
            McpServerRegistry,
            McpWatchlist.server_id == McpServerRegistry.server_id,
        )
        .where(McpWatchlist.org_id == org_id)
    )
    result = await db.execute(stmt)
    rows = result.fetchall()

    items = []
    for row in rows:
        server_id, name, risk_tier, last_assessed = row
        verdict = trust_gate(server_id)
        items.append(
            WatchlistItem(
                server_id=server_id,
                name=name,
                risk_tier=risk_tier,
                verdict=verdict,
                last_assessed=last_assessed,
            )
        )
    return WatchlistResponse(items=items)


async def add_to_watchlist(
    payload: Dict[str, Any],
    current_user=Depends(),
    db: AsyncSession = Depends(get_session),
) -> Dict[str, str]:
    """Add a server to the organisation's watchlist (upsert)."""
    org_id = getattr(current_user, "org_id", None)
    added_by = getattr(current_user, "id", None)
    if org_id is None or added_by is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User information missing",
        )

    server_id = payload.get("server_id")
    if server_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="server_id required",
        )

    stmt = (
        insert(McpWatchlist)
        .values(
            server_id=server_id,
            org_id=org_id,
            added_by=added_by,
            added_at=datetime.datetime.utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["server_id", "org_id"],
            set_={"added_by": added_by, "added_at": datetime.datetime.utcnow()},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "added"}


async def remove_from_watchlist(
    server_id: int,
    current_user=Depends(),
    db: AsyncSession = Depends(get_session),
) -> Dict[str, str]:
    """Remove a server from the organisation's watchlist."""
    org_id = getattr(current_user, "org_id", None)
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User organisation not found",
        )

    stmt = delete(McpWatchlist).where(
        McpWatchlist.server_id == server_id, McpWatchlist.org_id == org_id
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist entry not found",
        )
    await db.commit()
    return {"status": "removed"}


async def get_risk_detail(
    server_id: int,
    current_user=Depends(),
    db: AsyncSession = Depends(get_session),
) -> RiskDetailResponse:
    """Return the full risk breakdown for a watchlisted server."""
    org_id = getattr(current_user, "org_id", None)
    if org_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User organisation not found",
        )

    # Ensure the server belongs to the org (via watchlist)
    watch_stmt = select(McpWatchlist).where(
        McpWatchlist.server_id == server_id, McpWatchlist.org_id == org_id
    )
    watch_res = await db.execute(watch_stmt)
    if watch_res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not in watchlist",
        )

    # Pull axis scores
    axis_stmt = select(
        McpLlmAxisScore.axis,
        McpLlmAxisScore.score,
    ).where(McpLlmAxisScore.server_id == server_id)
    axis_res = await db.execute(axis_stmt)
    axis_rows = axis_res.fetchall()

    axes = [AxisScore(axis=row[0], score=row[1]) for row in axis_rows]

    verdict = trust_gate(server_id)

    return RiskDetailResponse(server_id=server_id, axes=axes, verdict=verdict)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    # ------------------------------------------------------------------- #
    # Helper: a minimal user object mimicking the JWT payload
    # ------------------------------------------------------------------- #
    class _FakeUser:
        def __init__(self, org_id: int, user_id: int):
            self.org_id = org_id
            self.id = user_id

    # ------------------------------------------------------------------- #
    # Override get_session to point at an in‑memory SQLite DB for the test
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _test_get_session():
        async with TestSession() as session:
            yield session

    # ------------------------------------------------------------------- #
    # Build a tiny FastAPI app that includes the router (if any)
    # ------------------------------------------------------------------- #
    app = FastAPI()

    # The router for this service lives alongside this module; import lazily
    from services.staged.verdict_watchlist.router import router as watchlist_router

    app.include_router(watchlist_router, prefix="/api")

    # Apply the dependency override
    app.dependency_overrides[get_session] = _test_get_session

    client = TestClient(app)

    async def _seed_data():
        async with TestSession() as session:
            # two servers
            await session.execute(
                insert(McpServerRegistry).values(
                    [
                        {
                            "server_id": 1,
                            "name": "alpha",
                            "risk_tier": "high",
                            "last_assessed": datetime.datetime.utcnow(),
                        },
                        {
                            "server_id": 2,
                            "name": "beta",
                            "risk_tier": "low",
                            "last_assessed": datetime.datetime.utcnow(),
                        },
                    ]
                )
            )
            await session.commit()

    async def _run_test():
        await _seed_data()
        # add both servers to watchlist via POST
        resp = client.post(
            "/api/watchlist",
            json={"server_id": 1},
            headers={"Authorization": "Bearer fake"},
        )
        assert resp.status_code == 200

        resp = client.post(
            "/api/watchlist",
            json={"server_id": 2},
            headers={"Authorization": "Bearer fake"},
        )
        assert resp.status_code == 200

        # GET watchlist
        resp = client.get(
            "/api/watchlist", headers={"Authorization": "Bearer fake"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert len(data["items"]) > 0
        first = data["items"][0]
        assert "server_id" in first
        assert "risk_tier" in first

    # ------------------------------------------------------------------- #
    # Run the async test harness
    # ------------------------------------------------------------------- #
    asyncio.run(_run_test())
    print("PASS")