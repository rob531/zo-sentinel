"""
services/staged/registry_growth_progress/contract.py

FastAPI contract for the `registry_growth_progress` service.
Provides an endpoint that returns the count of servers grouped by their
`first_seen` date.
"""

from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports – must remain unchanged for production use
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class GrowthDateCount(BaseModel):
    date: datetime.date = Field(..., description="The date a server was first seen")
    count: int = Field(..., description="Number of servers first seen on that date")


class GrowthResponse(BaseModel):
    dates: List[GrowthDateCount] = Field(..., description="Growth data grouped by date")


@router.get(
    "/registry/growth",
    response_model=GrowthResponse,
    summary="Growth of server registry over time",
)
def get_registry_growth(session: Session = Depends(get_session)) -> GrowthResponse:
    """
    Return the number of servers first seen on each distinct date.
    """
    stmt = (
        select(
            func.date(McpServerRegistry.first_seen).label("date"),
            func.count().label("count"),
        )
        .group_by(func.date(McpServerRegistry.first_seen))
        .order_by(func.date(McpServerRegistry.first_seen))
    )
    results = session.execute(stmt).all()
    dates = [
        GrowthDateCount(date=row.date, count=row.count)  # type: ignore[arg-type]
        for row in results
    ]
    return GrowthResponse(dates=dates)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.registry_growth_progress.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a throwaway SQLite in‑memory DB for the test
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    TEST_DB_URL = "sqlite:///:memory:"
    test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=test_engine)

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Dependency override that supplies a session bound to the test engine
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Assemble FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # Apply the dependency override
    app.dependency_overrides[get_session] = get_test_session

    # Seed the in‑memory DB with deterministic data
    with TestSessionLocal() as db:
        dates = [
            datetime.datetime(2023, 1, 1),
            datetime.datetime(2023, 1, 2),
            datetime.datetime(2023, 1, 3),
        ]
        # 10 servers spread across the three dates
        for i in range(10):
            server = McpServerRegistry(
                server_id=f"server{i}",
                first_seen=dates[i % len(dates)],
            )
            db.add(server)
        db.commit()

    # Run the test client against the endpoint
    client = TestClient(app)
    resp = client.get("/api/registry/growth")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "dates" in data, "Response missing 'dates' key"

    # Verify that the count for 2023‑01‑01 matches the seeded data (should be 4)
    target_date = "2023-01-01"
    matching = [d for d in data["dates"] if d["date"] == target_date]
    assert matching, f"No entry for date {target_date}"
    assert matching[0]["count"] == 4, f"Expected 4 servers on {target_date}, got {matching[0]['count']}"

    print("PASS")