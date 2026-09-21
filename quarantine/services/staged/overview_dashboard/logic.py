# services/staged/overview_dashboard/logic.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
import json
from typing import List, Dict, Any

from app.db import get_session

router = APIRouter()


async def get_overview_dashboard(session=Depends(get_session)) -> Dict[str, Any]:
    # Fetch the most recent summary row
    summary_sql = text(
        """
        SELECT total_servers, tier_distribution
        FROM mcp_risk_tier_summary
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    summary_row = session.execute(summary_sql).first()
    if summary_row is None:
        summary = {"total_servers": 0, "tier_distribution": []}
    else:
        total_servers = summary_row.total_servers
        # tier_distribution is stored as JSON text
        tier_distribution = json.loads(summary_row.tier_distribution)
        summary = {"total_servers": total_servers, "tier_distribution": tier_distribution}

    # Fetch the trend data
    trend_sql = text(
        """
        SELECT date, tier, count
        FROM mcp_risk_tier_trend
        ORDER BY date ASC
        """
    )
    trend_rows = session.execute(trend_sql).fetchall()
    series: List[Dict[str, Any]] = [
        {"date": str(row.date), "tier": row.tier, "count": row.count} for row in trend_rows
    ]
    trend = {"days": len(series), "series": series}

    return {"summary": summary, "trend": trend}


@router.get("/api/overview/dashboard")
async def overview_dashboard_endpoint(session=Depends(get_session)):
    return await get_overview_dashboard(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Table, Column, Integer, Text, Date, MetaData
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build a tiny in‑memory SQLite DB that mimics the required tables
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    metadata = MetaData()

    summary_tbl = Table(
        "mcp_risk_tier_summary",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("total_servers", Integer, nullable=False),
        Column("tier_distribution", Text, nullable=False),  # JSON stored as TEXT
        Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    )

    trend_tbl = Table(
        "mcp_risk_tier_trend",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("date", Date, nullable=False),
        Column("tier", Text, nullable=False),
        Column("count", Integer, nullable=False),
    )

    metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed the DB with deterministic test data
    # ------------------------------------------------------------------- #
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as sess:
        sess.execute(
            summary_tbl.insert(),
            {
                "total_servers": 100,
                "tier_distribution": json.dumps(
                    [
                        {"tier": "high", "count": 30},
                        {"tier": "medium", "count": 50},
                        {"tier": "low", "count": 20},
                    ]
                ),
            },
        )
        sess.execute(
            trend_tbl.insert(),
            [
                {"date": "2024-07-25", "tier": "high", "count": 10},
                {"date": "2024-07-26", "tier": "medium", "count": 20},
                {"date": "2024-07-27", "tier": "low", "count": 15},
            ],
        )
        sess.commit()

    # ------------------------------------------------------------------- #
    # Override the app's DB dependency to use the in‑memory session
    # ------------------------------------------------------------------- #
    from app.db import get_session as original_get_session

    def get_test_session():
        with SessionLocal() as test_sess:
            yield test_sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[original_get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/overview/dashboard")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["summary"]["total_servers"] == 100
        assert len(data["summary"]["tier_distribution"]) == 3
        assert data["trend"]["days"] == 3
        assert len(data["trend"]["series"]) == 3
    except AssertionError:
        print("FAIL: Response content mismatch", file=sys.stderr)
        sys.exit(1)

    print("PASS")