# deps: fastapi, sqlalchemy, requests
"""Risk Tier Trend Service

Provides an API endpoint to retrieve risk tier trend data aggregated over time.
Reads from mcp_signal_scores (mesh table via write_service) and mcp_server_registry
(app table via app.db session).
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_trend_service"])


def _query_mesh(sql: str, params: Optional[dict] = None) -> list:
    """Execute a read-only query against the ZoComputer mesh store."""
    import requests as _req
    resp = _req.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}},
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Mesh query failed")
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=502, detail=data["error"])
    return data if isinstance(data, list) else []


@router.get("/risk_tier_trend")
def get_risk_tier_trend(
    session: Session = Depends(get_session),
    days: int = Query(default=30, ge=1, le=365),
    server_id: Optional[str] = None,
) -> list[dict]:
    """Return risk tier distribution over time.

    - days: lookback window (default 30, max 365)
    - server_id: optional filter to a specific server
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    if server_id:
        sql = """
            SELECT
                risk_tier,
                DATE(assessed_at) AS day,
                COUNT(*) AS count
            FROM mcp_signal_scores
            WHERE server_id = :server_id
              AND assessed_at >= :cutoff
            GROUP BY risk_tier, DATE(assessed_at)
            ORDER BY day ASC, risk_tier
        """
        rows = _query_mesh(sql, {"server_id": server_id, "cutoff": cutoff})
    else:
        sql = """
            SELECT
                risk_tier,
                DATE(assessed_at) AS day,
                COUNT(*) AS count
            FROM mcp_signal_scores
            WHERE assessed_at >= :cutoff
            GROUP BY risk_tier, DATE(assessed_at)
            ORDER BY day ASC, risk_tier
        """
        rows = _query_mesh(sql, {"cutoff": cutoff})

    # Normalise None/null risk_tier → "UNKNOWN"
    for r in rows:
        r.setdefault("risk_tier", "UNKNOWN")

    return rows


@router.get("/risk_tier_summary")
def get_risk_tier_summary(
    session: Session = Depends(get_session),
) -> dict:
    """Return current snapshot of risk tier counts from the app registry."""
    result = session.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        ).group_by(McpServerRegistry.risk_tier)
    )
    rows = result.all()
    total = sum(r.count for r in rows)
    return {
        "summary": [
            {"tier": r.risk_tier or "UNKNOWN", "count": r.count, "pct": round(r.count / total * 100, 1) if total else 0}
            for r in rows
        ],
        "total": total,
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In-memory SQLite for self-test (app.db override)
    engine = create_engine("sqlite:///:memory:")
    from app.models import Base
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    from app.db import get_session as _orig
    import app.db as _db

    def _override():
        return TestingSession()

    _db.get_session = _override

    client = TestClient(router)

    # smoke: empty response when no data
    r = client.get("/risk_tier_trend")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list), "Expected list"

    r2 = client.get("/risk_tier_summary")
    assert r2.status_code == 200, r2.text
    assert "summary" in r2.json()

    print("Self-test passed")