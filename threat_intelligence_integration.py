from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock
from app.db import get_session
from app.models import ThreatIntelRef


class ThreatIntelRecord(BaseModel):
    indicator_type: str
    indicator_value: str
    source: str
    source_url: str | None = None
    pulse_id: str | None = None
    pulse_name: str | None = None
    pulse_created: str | None = None
    is_aggregator: bool = False


class ThreatIntelService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def fetch_from_source(self, source_url: str) -> list[ThreatIntelRecord]:
        pass

    async def upsert_threat_intel(self, record: ThreatIntelRecord) -> ThreatIntelRef:
        stmt = select(ThreatIntelRef).where(
            ThreatIntelRef.indicator_type == record.indicator_type,
            ThreatIntelRef.indicator_value == record.indicator_value,
            ThreatIntelRef.source == record.source,
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.pulse_id = record.pulse_id
            existing.pulse_name = record.pulse_name
            existing.pulse_created = record.pulse_created
            existing.source_url = record.source_url
            existing.is_aggregator = record.is_aggregator
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        new_ref = ThreatIntelRef(
            indicator_type=record.indicator_type,
            indicator_value=record.indicator_value,
            source=record.source,
            source_url=record.source_url,
            pulse_id=record.pulse_id,
            pulse_name=record.pulse_name,
            pulse_created=record.pulse_created,
            is_aggregator=record.is_aggregator,
        )
        self.session.add(new_ref)
        await self.session.commit()
        await self.session.refresh(new_ref)
        return new_ref

    async def sync_from_source(self, source_url: str, source_name: str) -> list[ThreatIntelRef]:
        records = await self.fetch_from_source(source_url)
        results = []
        for rec_data in records:
            record = ThreatIntelRecord(
                indicator_type=rec_data.get("indicator_type"),
                indicator_value=rec_data.get("indicator_value"),
                source=source_name,
                source_url=source_url,
                pulse_id=rec_data.get("pulse_id"),
                pulse_name=rec_data.get("pulse_name"),
                pulse_created=rec_data.get("pulse_created"),
                is_aggregator=rec_data.get("is_aggregator", False),
            )
            ref = await self.upsert_threat_intel(record)
            results.append(ref)
        return results


async def _run_self_test():
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text(
            "CREATE TABLE threat_intel_refs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "indicator_type TEXT NOT NULL, "
            "indicator_value TEXT NOT NULL, "
            "source TEXT NOT NULL, "
            "source_url TEXT, "
            "pulse_id TEXT, "
            "pulse_name TEXT, "
            "pulse_created TEXT, "
            "is_aggregator INTEGER DEFAULT 0, "
            "fetched_at TEXT"
            ")"
        ))

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with async_session() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    mock_records = [
        {
            "indicator_type": "ipv4",
            "indicator_value": "192.0.2.1",
            "pulse_id": "abc123",
            "pulse_name": "Malicious IP",
            "pulse_created": "2024-01-15T10:00:00Z",
            "is_aggregator": False,
        },
        {
            "indicator_type": "domain",
            "indicator_value": "evil.example.com",
            "pulse_id": "def456",
            "pulse_name": "C2 Domain",
            "pulse_created": "2024-01-16T12:30:00Z",
            "is_aggregator": True,
        },
    ]

    async def mock_fetch(source_url: str) -> list[dict]:
        return mock_records

    async with async_session() as session:
        service = ThreatIntelService(session)
        service.fetch_from_source = mock_fetch

        with patch.object(ThreatIntelService, "fetch_from_source", mock_fetch):
            refs = await service.sync_from_source(
                "https://threatfeed.example.com/api",
                "TestFeed"
            )

    assert len(refs) == 2, f"Expected 2 refs, got {len(refs)}"

    async with async_session() as session:
        result = await session.execute(select(ThreatIntelRef))
        all_refs = result.scalars().all()

    assert len(all_refs) == 2, f"Expected 2 records in DB, got {len(all_refs)}"

    ip_ref = next((r for r in all_refs if r.indicator_value == "192.0.2.1"), None)
    assert ip_ref is not None, "Missing IPv4 record"
    assert ip_ref.indicator_type == "ipv4"
    assert ip_ref.source == "TestFeed"
    assert ip_ref.pulse_name == "Malicious IP"

    domain_ref = next((r for r in all_refs if r.indicator_value == "evil.example.com"), None)
    assert domain_ref is not None, "Missing domain record"
    assert domain_ref.indicator_type == "domain"
    assert domain_ref.is_aggregator == True

    await engine.dispose()
    print("PASS")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run_self_test())