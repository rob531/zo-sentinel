import datetime
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class FreshnessMetadataOut(BaseModel):
    server_id: str
    first_seen: datetime.datetime
    last_seen: datetime.datetime
    last_scanned: datetime.datetime
    scan_count: int
    last_assessed: datetime.datetime
    age_hours: float
    is_stale: bool


def get_freshness(
    server_id: str,
    db: Session = Depends(get_session),
) -> FreshnessMetadataOut:
    """Return freshness metadata for a given server."""
    record: Optional[McpServerRegistry] = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Server not found")

    now = datetime.datetime.utcnow()
    age_seconds = (now - record.last_assessed).total_seconds()
    age_hours = age_seconds / 3600.0
    is_stale = age_hours > 168.0

    return FreshnessMetadataOut(
        server_id=record.server_id,
        first_seen=record.first_seen,
        last_seen=record.last_seen,
        last_scanned=record.last_scanned,
        scan_count=record.scan_count,
        last_assessed=record.last_assessed,
        age_hours=age_hours,
        is_stale=is_stale,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for isolated testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    McpServerRegistry.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Helper to provide a session that mimics the FastAPI dependency
    def get_test_session() -> Session:
        return SessionLocal()

    # Seed four servers with deterministic timestamps
    now = datetime.datetime.utcnow()
    seed_data = [
        {
            "server_id": "s1",
            "first_seen": now - datetime.timedelta(days=10),
            "last_seen": now - datetime.timedelta(days=1),
            "last_scanned": now - datetime.timedelta(hours=5),
            "scan_count": 5,
            "last_assessed": now - datetime.timedelta(hours=100),
        },
        {
            "server_id": "s2",
            "first_seen": now - datetime.timedelta(days=20),
            "last_seen": now - datetime.timedelta(days=2),
            "last_scanned": now - datetime.timedelta(hours=10),
            "scan_count": 10,
            "last_assessed": now - datetime.timedelta(hours=50),
        },
        {
            "server_id": "s3",
            "first_seen": now - datetime.timedelta(days=5),
            "last_seen": now - datetime.timedelta(hours=1),
            "last_scanned": now - datetime.timedelta(hours=1),
            "scan_count": 2,
            "last_assessed": now - datetime.timedelta(hours=200),
        },
        {
            "server_id": "s4",
            "first_seen": now - datetime.timedelta(days=1),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "last_assessed": now - datetime.timedelta(hours=10),
        },
    ]

    # Insert seed rows
    with SessionLocal() as db:
        for row in seed_data:
            db.add(McpServerRegistry(**row))
        db.commit()

    async def run_self_test() -> None:
        # Verify each server's stale flag matches expectation
        for row in seed_data:
            result = get_freshness(row["server_id"], db=get_test_session())
            expected_age = (now - row["last_assessed"]).total_seconds() / 3600.0
            expected_stale = expected_age > 168.0
            assert abs(result.age_hours - expected_age) < 0.001
            assert result.is_stale == expected_stale, f"{row['server_id']} stale flag mismatch"
        print("PASS")

    asyncio.run(run_self_test())