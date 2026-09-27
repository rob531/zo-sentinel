"""services/staged/server_age_cohort_analysis/contract.py

FastAPI contract for the *server_age_cohort_analysis* service.

Provides:
    GET /api/registry/cohorts?windows=7d,30d,90d

The endpoint returns age‑cohort statistics for servers stored in the
`mcp_server_registry` table.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Real application data‑layer imports (must not be stubbed)
# --------------------------------------------------------------------------- #
from app.db import Base, get_session
from app.models import McpServerRegistry

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class Cohort(BaseModel):
    """Statistics for a single cohort."""
    label: str
    window: str
    total: int
    by_tier: Dict[str, int]


class CohortResponse(BaseModel):
    """Top‑level response payload."""
    cohorts: List[Cohort]
    totals: Dict[str, int]


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


def _parse_windows(windows: str) -> List[int]:
    """Convert a comma‑separated list like ``'7d,30d,90d'`` to a sorted list of ints."""
    days = []
    for part in windows.split(","):
        part = part.strip().lower()
        if part.endswith("d"):
            part = part[:-1]
        if part.isdigit():
            days.append(int(part))
    return sorted(set(days))


def _build_ranges(days: List[int]) -> List[tuple[int, int | None]]:
    """
    From a sorted list of day thresholds produce inclusive ranges.

    Example: [7, 30, 90] → [(0, 7), (7, 30), (30, 90), (90, None)]
    """
    ranges: List[tuple[int, int | None]] = []
    start = 0
    for d in days:
        ranges.append((start, d))
        start = d
    ranges.append((start, None))  # open‑ended final bucket
    return ranges


def _label_for_range(start: int, end: int | None) -> str:
    if end is None:
        return f">{start}d"
    return f"{start}-{end}d"


@router.get(
    "/registry/cohorts",
    response_model=CohortResponse,
    summary="Age‑cohort analysis for registered servers",
)
def get_cohorts(
    windows: str = Query("7d,30d,90d", description="Comma‑separated list of day windows, e.g. '7d,30d,90d'"),
    db: Session = Depends(get_session),
) -> CohortResponse:
    """
    Return server counts grouped by age windows (based on ``first_seen``) and
    broken down by ``risk_tier``.
    """
    now = datetime.utcnow()
    day_thresholds = _parse_windows(windows)
    if not day_thresholds:
        day_thresholds = [7, 30, 90]

    ranges = _build_ranges(day_thresholds)

    # Initialise cohort containers
    cohort_data: Dict[str, Dict] = {}
    for start, end in ranges:
        label = _label_for_range(start, end)
        cohort_data[label] = {"label": label, "window": f"{start}-{end if end else '∞'}d", "total": 0, "by_tier": {}}

    total_servers = 0
    never_seen_7d = 0

    for srv in db.query(McpServerRegistry).all():
        total_servers += 1

        # ---- freshness metric for totals ----
        if not srv.last_seen:
            never_seen_7d += 1
        else:
            if (now - srv.last_seen) > timedelta(days=7):
                never_seen_7d += 1

        # ---- age cohort ----
        if not srv.first_seen:
            # treat missing first_seen as oldest bucket
            bucket_label = _label_for_range(ranges[-1][0], None)
        else:
            age_days = (now - srv.first_seen).days
            bucket_label = None
            for start, end in ranges:
                if end is None:
                    if age_days >= start:
                        bucket_label = _label_for_range(start, None)
                        break
                elif start <= age_days < end:
                    bucket_label = _label_for_range(start, end)
                    break
            if bucket_label is None:
                # fallback to last bucket
                bucket_label = _label_for_range(ranges[-1][0], None)

        cohort = cohort_data[bucket_label]
        cohort["total"] += 1
        tier = srv.risk_tier or "UNKNOWN"
        cohort["by_tier"][tier] = cohort["by_tier"].get(tier, 0) + 1

    # Convert dicts to Pydantic models
    cohorts = [
        Cohort(
            label=v["label"],
            window=v["window"],
            total=v["total"],
            by_tier=v["by_tier"],
        )
        for v in cohort_data.values()
    ]

    totals = {"total_servers": total_servers, "never_seen_7d": never_seen_7d}
    return CohortResponse(cohorts=cohorts, totals=totals)


# --------------------------------------------------------------------------- #
# Self‑test (executed via ``python -m services.staged.server_age_cohort_analysis.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app for the test
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (StaticPool) – overrides the real DB dependency
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Dependency override
    def _override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data (5 servers spanning three age cohorts, mixed tiers)
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    seed = [
        McpServerRegistry(
            server_id="srv-1",
            first_seen=now - timedelta(days=2),
            last_seen=now - timedelta(days=1),
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id="srv-2",
            first_seen=now - timedelta(days=10),
            last_seen=now - timedelta(days=5),
            risk_tier="ENTERPRISE_CONTROLLED",
        ),
        McpServerRegistry(
            server_id="srv-3",
            first_seen=now - timedelta(days=45),
            last_seen=now - timedelta(days=30),
            risk_tier="TRUSTED_GENERAL",
        ),
        McpServerRegistry(
            server_id="srv-4",
            first_seen=now - timedelta(days=120),
            last_seen=now - timedelta(days=100),
            risk_tier="ENTERPRISE_CONTROLLED",
        ),
        McpServerRegistry(
            server_id="srv-5",
            first_seen=now - timedelta(days=200),
            last_seen=now - timedelta(days=190),
            risk_tier="TRUSTED_GENERAL",
        ),
    ]

    # Insert seed data
    with TestSessionLocal() as db:
        db.add_all(seed)
        db.commit()

    # ------------------------------------------------------------------- #
    # Run the test client
    # ------------------------------------------------------------------- #
    from fastapi.testclient import TestClient

    client = TestClient(app)

    resp = client.get("/api/registry/cohorts?windows=7d,30d,90d")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    # Verify a cohort label for the 30‑90d window exists
    labels = [c["label"] for c in payload["cohorts"]]
    assert any("30-90d" in lbl for lbl in labels), "Missing '30-90d' cohort label"

    # Verify at least one cohort reports a non‑zero total
    assert any(c["total"] > 0 for c in payload["cohorts"]), "All cohorts have zero total"

    print("PASS")
    sys.exit(0)