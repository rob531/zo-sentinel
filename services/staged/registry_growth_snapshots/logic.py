# services/staged/registry_growth_snapshots/logic.py
from collections import defaultdict
from datetime import date
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry  # type: ignore

router = APIRouter(prefix="/api")


class SourceCount(BaseModel):
    name: str
    count: int


class Snapshot(BaseModel):
    date: date
    count: int
    sources: List[SourceCount]


class SnapshotsResponse(BaseModel):
    snapshots: List[Snapshot]


def _compute_snapshots(session: Session) -> List[Snapshot]:
    rows = session.query(McpServerRegistry).all()

    daily_total: defaultdict[date, int] = defaultdict(int)
    daily_sources: defaultdict[date, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for row in rows:
        # Expect the model to have `created_at` (datetime) and `source` (str) columns
        row_date: date = row.created_at.date()
        source_name: str = getattr(row, "source", "unknown")

        daily_total[row_date] += 1
        daily_sources[row_date][source_name] += 1

    # Build cumulative count
    snapshots: List[Snapshot] = []
    cumulative = 0
    for d in sorted(daily_total):
        cumulative += daily_total[d]
        sources_list = [
            SourceCount(name=name, count=count)
            for name, count in daily_sources[d].items()
        ]
        snapshots.append(
            Snapshot(date=d, count=cumulative, sources=sources_list)
        )
    return snapshots


@router.get(
    "/registry/growth/snapshots",
    response_model=SnapshotsResponse,
    name="registry_growth_snapshots",
)
async def registry_growth_snapshots(
    session: Session = Depends(get_session),
) -> SnapshotsResponse:
    snapshots = _compute_snapshots(session)
    return SnapshotsResponse(snapshots=snapshots)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the app models to it
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)

    # Override the FastAPI dependency to use the test session
    def get_test_session() -> Session:
        return SessionLocal()

    # Seed two days of data
    test_session = SessionLocal()
    try:
        # Assume the model has columns: id (PK), created_at (datetime), source (str)
        from datetime import datetime, timedelta

        class _Row:
            """Simple namespace to mimic the ORM object for seeding."""
            pass

        # Day 1
        for i in range(3):
            row = McpServerRegistry()
            row.created_at = datetime.utcnow() - timedelta(days=2)
            row.source = "source_a" if i % 2 == 0 else "source_b"
            test_session.add(row)

        # Day 2
        for i in range(2):
            row = McpServerRegistry()
            row.created_at = datetime.utcnow() - timedelta(days=1)
            row.source = "source_a"
            test_session.add(row)

        test_session.commit()

        # Run the logic
        snapshots = _compute_snapshots(test_session)

        # Acceptance: non‑empty array with both snapshots present
        assert len(snapshots) >= 2, "Expected at least two snapshots"
        assert snapshots[0].date != snapshots[1].date, "Snapshots dates should differ"
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        test_session.close()