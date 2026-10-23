# services/staged/server_age_cohort_analysis/logic.py
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


def _parse_windows(windows: str) -> List[int]:
    """Parse a comma‑separated list like ``7d,30d,90d`` into a list of ints."""
    days = []
    for w in windows.split(","):
        w = w.strip().lower()
        if w.endswith("d"):
            w = w[:-1]
        if w.isdigit():
            days.append(int(w))
    return sorted(set(days))


def _build_intervals(days: List[int]) -> List[Dict[str, Optional[int]]]:
    """Create interval dicts ``{'lower': int, 'upper': Optional[int]}``."""
    intervals = []
    lower = 0
    for d in days:
        intervals.append({"lower": lower, "upper": d})
        lower = d
    intervals.append({"lower": lower, "upper": None})  # open‑ended last bucket
    return intervals


def _label_for_interval(lower: int, upper: Optional[int]) -> str:
    if upper is None:
        return f"{lower}d+"
    return f"{lower}-{upper}d"


@router.get("/registry/cohorts")
def get_cohorts(
    windows: str = Query("7d,30d,90d", description="comma‑separated windows, e.g. 7d,30d,90d"),
    session: Session = Depends(get_session),
):
    """Return server age and freshness cohorts."""
    now = datetime.utcnow()

    # Load needed columns only
    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
        )
        .filter(McpServerRegistry.server_id.isnot(None))
        .all()
    )

    # Prepare windows
    days = _parse_windows(windows)
    intervals = _build_intervals(days)

    # Helper to compute days delta safely
    def _days_since(ts: Optional[datetime]) -> Optional[int]:
        if ts is None:
            return None
        return (now - ts).days

    # Build cohort data
    cohorts: List[Dict] = []
    for interval in intervals:
        lower = interval["lower"]
        upper = interval["upper"]
        label = _label_for_interval(lower, upper)

        total = 0
        by_tier: Dict[str, int] = {}

        for row in rows:
            age_days = _days_since(row.first_seen)
            if age_days is None:
                continue
            if age_days < lower:
                continue
            if upper is not None and age_days >= upper:
                continue

            total += 1
            tier = row.risk_tier or "UNKNOWN"
            by_tier[tier] = by_tier.get(tier, 0) + 1

        cohorts.append(
            {
                "label": label,
                "window": label,
                "total": total,
                "by_tier": by_tier,
            }
        )

    total_servers = len(rows)
    never_seen_7d = sum(
        1
        for row in rows
        if row.last_seen is None or (now - row.last_seen).days > 7
    )

    return {"cohorts": cohorts, "totals": {"total_servers": total_servers, "never_seen_7d": never_seen_7d}}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine compatible with the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the models we use
    McpServerRegistry.__table__.create(bind=engine)

    # Seed data: 5 servers across three age cohorts, mixed risk tiers
    now = datetime.utcnow()
    seed = [
        # 0‑7d cohort, TRUSTED_GENERAL
        {
            "server_id": "srv-1",
            "first_seen": now - timedelta(days=2),
            "last_seen": now - timedelta(days=1),
            "risk_tier": "TRUSTED_GENERAL",
        },
        # 7‑30d cohort, ENTERPRISE_CONTROLLED
        {
            "server_id": "srv-2",
            "first_seen": now - timedelta(days=10),
            "last_seen": now - timedelta(days=5),
            "risk_tier": "ENTERPRISE_CONTROLLED",
        },
        # 30‑90d cohort, UNTRUSTED
        {
            "server_id": "srv-3",
            "first_seen": now - timedelta(days=45),
            "last_seen": now - timedelta(days=20),
            "risk_tier": "UNTRUSTED",
        },
        # 90d+ cohort, TRUSTED_GENERAL
        {
            "server_id": "srv-4",
            "first_seen": now - timedelta(days=120),
            "last_seen": now - timedelta(days=60),
            "risk_tier": "TRUSTED_GENERAL",
        },
        # 30‑90d cohort, ENTERPRISE_CONTROLLED (freshness >7d)
        {
            "server_id": "srv-5",
            "first_seen": now - timedelta(days=60),
            "last_seen": now - timedelta(days=10),
            "risk_tier": "ENTERPRISE_CONTROLLED",
        },
    ]

    db: Session = SessionLocal()
    for rec in seed:
        db.add(
            McpServerRegistry(
                server_id=rec["server_id"],
                first_seen=rec["first_seen"],
                last_seen=rec["last_seen"],
                risk_tier=rec["risk_tier"],
            )
        )
    db.commit()
    db.close()

    # Override the dependency to use our in‑memory session
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/registry/cohorts?windows=7d,30d,90d")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "cohorts" in data, "Missing cohorts key"
    labels = [c["label"] for c in data["cohorts"]]
    assert any("30-90d" in lbl for lbl in labels), "Missing 30-90d label"
    assert any(c["total"] > 0 for c in data["cohorts"]), "All cohort totals are zero"
    print("PASS")
    sys.exit(0)