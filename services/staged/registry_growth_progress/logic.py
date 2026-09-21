import datetime
from typing import List

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry


class DateCount(BaseModel):
    date: datetime.date = Field(..., description="Date of first_seen")
    count: int = Field(..., description="Number of servers first seen on this date")


class RegistryGrowthProgressResponse(BaseModel):
    dates: List[DateCount] = Field(..., description="Growth histogram by date")


def _process_date(value):
    """Normalize date returned by different DB back‑ends."""
    if isinstance(value, datetime.date):
        return value
    # SQLite returns a string like 'YYYY-MM-DD'
    if isinstance(value, str):
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    # Fallback – try to cast
    return datetime.date(value)


async def get_registry_growth_progress(
    session: Session = Depends(get_session),
) -> RegistryGrowthProgressResponse:
    """
    Return a histogram of server registrations grouped by the day of
    ``first_seen``.
    """
    stmt = (
        select(
            func.date(McpServerRegistry.first_seen).label("date"),
            func.count().label("cnt"),
        )
        .group_by("date")
        .order_by("date")
    )
    rows = session.execute(stmt).all()
    dates = [
        DateCount(date=_process_date(row[0]), count=row[1]) for row in rows
    ]
    return RegistryGrowthProgressResponse(dates=dates)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, future=True)

    def get_test_session():
        with TestSession() as s:
            yield s

    # ------------------------------------------------------------------- #
    # Seed 10 servers spread over three distinct dates
    # ------------------------------------------------------------------- #
    seed_dates = [
        datetime.date(2023, 1, 1),
        datetime.date(2023, 1, 2),
        datetime.date(2023, 1, 3),
    ]

    with TestSession() as s:
        for i in range(10):
            entry = McpServerRegistry(
                server_id=f"server-{i}",
                first_seen=datetime.datetime.combine(
                    seed_dates[i % 3], datetime.time.min
                ),
            )
            s.add(entry)
        s.commit()

    # ------------------------------------------------------------------- #
    # Run the logic and validate the result
    # ------------------------------------------------------------------- #
    async def _run():
        # Direct call bypassing FastAPI Depends
        result = await get_registry_growth_progress(session=TestSession())
        assert isinstance(result, RegistryGrowthProgressResponse)
        # Expect three distinct dates
        assert len(result.dates) == 3
        # Count for the first seeded date should be 4 (10 % 3 == 1 extra)
        for dc in result.dates:
            if dc.date == seed_dates[0]:
                assert dc.count == 4
                break
        else:
            assert False, "First date not found in result"
        print("PASS")

    asyncio.run(_run())