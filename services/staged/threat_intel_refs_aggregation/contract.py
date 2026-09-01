# services/staged/threat_intel_refs_aggregation/contract.py
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged)
from app.db import get_session
from app.models import ThreatIntelRef  # noqa: F401  (imported to satisfy module contract)

router = APIRouter()


class Pulse(BaseModel):
    id: int
    name: str
    created: datetime
    source: str


class IndicatorTypeAgg(BaseModel):
    type: str
    count: int
    pulses: List[Pulse] = []


class ThreatIntelRefsAggResponse(BaseModel):
    indicator_types: List[IndicatorTypeAgg]
    total_refs: int
    last_fetched: datetime | None = None


@router.get(
    "/threat-intel/refs",
    response_model=ThreatIntelRefsAggResponse,
    tags=["threat_intel_refs_aggregation"],
)
def get_threat_intel_refs_aggregation(
    session: Session = Depends(get_session),
) -> ThreatIntelRefsAggResponse:
    """Aggregate threat‑intel reference data."""
    total_refs = session.execute(text("SELECT COUNT(*) FROM threat_intel_refs")).scalar() or 0
    last_fetched = session.execute(text("SELECT MAX(fetched_at) FROM threat_intel_refs")).scalar()

    agg_rows = session.execute(
        text(
            """
            SELECT indicator_type, COUNT(*) AS cnt
            FROM threat_intel_refs
            GROUP BY indicator_type
            """
        )
    ).fetchall()

    indicator_types: List[IndicatorTypeAgg] = []
    for row in agg_rows:
        indicator_types.append(
            IndicatorTypeAgg(
                type=row["indicator_type"],
                count=row["cnt"],
                pulses=[],  # pulse details are omitted for brevity
            )
        )

    return ThreatIntelRefsAggResponse(
        indicator_types=indicator_types,
        total_refs=total_refs,
        last_fetched=last_fetched,
    )


app = FastAPI()
app.include_router(router, prefix="/api")


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.threat_intel_refs_aggregation.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime as dt

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB that mimics the real tables
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Minimal schema required for the test
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE threat_intel_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                indicator_type TEXT NOT NULL,
                fetched_at DATETIME NOT NULL
            );
            """
        )

    # Seed five rows across two indicator types
    now = dt.datetime.utcnow()
    seed = [
        {"indicator_type": "typeA", "fetched_at": now},
        {"indicator_type": "typeA", "fetched_at": now},
        {"indicator_type": "typeB", "fetched_at": now},
        {"indicator_type": "typeB", "fetched_at": now},
        {"indicator_type": "typeB", "fetched_at": now},
    ]
    with engine.begin() as conn:
        for row in seed:
            conn.execute(
                text(
                    """
                    INSERT INTO threat_intel_refs (indicator_type, fetched_at)
                    VALUES (:indicator_type, :fetched_at)
                    """
                ),
                {"indicator_type": row["indicator_type"], "fetched_at": row["fetched_at"]},
            )

    # ------------------------------------------------------------------- #
    # Override the FastAPI dependency to use the test session
    # ------------------------------------------------------------------- #
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the response
    # ------------------------------------------------------------------- #
    response = client.get("/api/threat-intel/refs")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()

    assert data["total_refs"] == 5, "total_refs mismatch"
    assert len(data["indicator_types"]) == 2, "indicator_types length mismatch"

    for it in data["indicator_types"]:
        if it["type"] == "typeA":
            assert it["count"] == 2, "typeA count mismatch"
        elif it["type"] == "typeB":
            assert it["count"] == 3, "typeB count mismatch"

    print("PASS")