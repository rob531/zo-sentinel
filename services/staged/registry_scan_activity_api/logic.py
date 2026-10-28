from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ScanActivityServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    scan_count: int
    last_scanned: Optional[datetime] = None
    risk_tier: Optional[str] = None


class ScanActivityResponse(BaseModel):
    days: int
    total_servers: int
    scanned_in_window: int
    never_scanned: int
    servers: List[ScanActivityServer]


@router.get(
    "/registry/scan-activity",
    response_model=ScanActivityResponse,
)
async def get_registry_scan_activity(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> ScanActivityResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_servers = int(
        await session.scalar(
            select(func.count()).select_from(McpServerRegistry)
        )
        or 0
    )
    never_scanned = int(
        await session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_scanned.is_(None))
        )
        or 0
    )
    scanned_in_window = int(
        await session.scalar(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_scanned >= cutoff)
        )
        or 0
    )

    result = await session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
            McpServerRegistry.risk_tier,
        )
        .order_by(
            McpServerRegistry.last_scanned.desc().nulls_last(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(200)
    )

    servers = [
        ScanActivityServer(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            scan_count=int(row.scan_count or 0),
            last_scanned=row.last_scanned,
            risk_tier=row.risk_tier,
        )
        for row in result.all()
    ]

    return ScanActivityResponse(
        days=days,
        total_servers=total_servers,
        scanned_in_window=scanned_in_window,
        never_scanned=never_scanned,
        servers=servers,
    )


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import asyncio

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    async def run_self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with engine.begin() as connection:
            await connection.run_sync(McpServerRegistry.__table__.create)

        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="scanned-inside",
                        name="Scanned Inside",
                        registry_source="test",
                        scan_count=2,
                        first_seen=now - timedelta(days=100),
                        last_seen=now - timedelta(days=1),
                        last_scanned=now - timedelta(days=2),
                        risk_tier="low",
                    ),
                    McpServerRegistry(
                        server_id="never-scanned",
                        name="Never Scanned",
                        registry_source="test",
                        scan_count=0,
                        first_seen=now - timedelta(days=100),
                        last_seen=now - timedelta(days=1),
                        last_scanned=None,
                        risk_tier=None,
                    ),
                    McpServerRegistry(
                        server_id="scanned-outside",
                        name="Scanned Outside",
                        registry_source="test",
                        scan_count=1,
                        first_seen=now - timedelta(days=100),
                        last_seen=now - timedelta(days=1),
                        last_scanned=now - timedelta(days=60),
                        risk_tier="medium",
                    ),
                ]
            )
            await session.commit()

        async def override_get_session():
            async with session_factory() as session:
                yield session

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_get_session

        transport = ASGITransport(app=that_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/registry/scan-activity?days=30")
            assert response.status_code == 200
            payload = response.json()
            assert payload["days"] == 30
            assert payload["total_servers"] == 3
            assert payload["scanned_in_window"] == 1
            assert payload["never_scanned"] == 1
            assert len(payload["servers"]) <= 200
            assert [server["server_id"] for server in payload["servers"]] == [
                "scanned-inside",
                "scanned-outside",
                "never-scanned",
            ]

        await engine.dispose()

    asyncio.run(run_self_test())
    print("PASS")