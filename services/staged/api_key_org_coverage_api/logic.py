from datetime import datetime, timezone

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ApiKey, Org


class OrgApiKeyCoverage(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    has_active_key: bool
    first_key_at: datetime | None


class ApiKeyCoverageSummary(BaseModel):
    total_orgs: int
    orgs_with_keys: int
    orgs_without_keys: int


class ApiKeyOrgCoverageResponse(BaseModel):
    orgs: list[OrgApiKeyCoverage]
    summary: ApiKeyCoverageSummary


async def get_api_key_org_coverage(
    session: AsyncSession = Depends(get_session),
) -> ApiKeyOrgCoverageResponse:
    rows = (
        await session.execute(
            select(
                Org.id.label("org_id"),
                Org.name.label("org_name"),
                func.count(ApiKey.id).label("key_count"),
                func.min(ApiKey.created_at).label("first_key_at"),
            )
            .select_from(Org)
            .outerjoin(ApiKey, ApiKey.org_id == Org.id)
            .group_by(Org.id, Org.name)
            .order_by(Org.id)
        )
    ).all()

    coverage = [
        OrgApiKeyCoverage(
            org_id=row.org_id,
            org_name=row.org_name,
            key_count=int(row.key_count or 0),
            has_active_key=int(row.key_count or 0) > 0,
            first_key_at=row.first_key_at,
        )
        for row in rows
    ]

    orgs_with_keys = sum(1 for org in coverage if org.has_active_key)
    total_orgs = len(coverage)

    return ApiKeyOrgCoverageResponse(
        orgs=coverage,
        summary=ApiKeyCoverageSummary(
            total_orgs=total_orgs,
            orgs_with_keys=orgs_with_keys,
            orgs_without_keys=total_orgs - orgs_with_keys,
        ),
    )


get_org_api_key_coverage = get_api_key_org_coverage
get_api_key_coverage = get_api_key_org_coverage
OrgKeyCoverage = OrgApiKeyCoverage
FleetSummary = ApiKeyCoverageSummary
ApiKeyCoverageResponse = ApiKeyOrgCoverageResponse


if __name__ == "__main__":
    import asyncio

    from fastapi import APIRouter, FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    async def self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Org.__table__.create)
            await connection.run_sync(ApiKey.__table__.create)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with session_factory() as session:
            session.add_all(
                [
                    Org(id=1, name="Alpha", created_at=now),
                    Org(id=2, name="Beta", created_at=now),
                    Org(id=3, name="Gamma", created_at=now),
                    ApiKey(
                        id=1,
                        org_id=1,
                        key_hash="hash-alpha",
                        label="Alpha primary",
                        created_at=now,
                    ),
                    ApiKey(
                        id=2,
                        org_id=2,
                        key_hash="hash-beta",
                        label="Beta primary",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

        async def override_get_session():
            async with session_factory() as session:
                yield session

        test_router = APIRouter(prefix="/orgs")

        @test_router.get("/api-key-coverage")
        async def api_key_coverage_endpoint(
            coverage: ApiKeyOrgCoverageResponse = Depends(get_api_key_org_coverage),
        ) -> ApiKeyOrgCoverageResponse:
            return coverage

        test_app = FastAPI()
        test_app.include_router(test_router, prefix="/api")
        test_app.dependency_overrides[get_session] = override_get_session

        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/orgs/api-key-coverage")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["summary"]["total_orgs"] == 3
            assert payload["summary"]["orgs_without_keys"] == 1
            assert payload["summary"]["orgs_with_keys"] == 2
            assert len(payload["orgs"]) == 3

        await engine.dispose()
        print("PASS")

    asyncio.run(self_test())