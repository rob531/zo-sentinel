"""
services.staged.server_freshness_by_source.contract
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Real data layer imports (must remain unchanged for production)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpServerRegistry, Base  # type: ignore

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------


class SourceStats(BaseModel):
    source: str = Field(..., alias="registry_source")
    total_servers: int
    scanned_last_7d: int
    scanned_last_30d: int
    never_scanned: int
    avg_days_since_scan: float | None
    tier_distribution: Dict[str, int]


class FreshnessResponse(BaseModel):
    sources: List[SourceStats]


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/registry/freshness-by-source",
    response_model=FreshnessResponse,
    name="get_freshness_by_source",
)
def get_freshness_by_source(session=Depends(get_session)):
    now = datetime.datetime.utcnow()
    seven_days_ago = now - datetime.timedelta(days=7)
    thirty_days_ago = now - datetime.timedelta(days=30)

    # Gather rows
    rows = session.query(McpServerRegistry).all()

    # Group by source
    grouped: Dict[str, List[McpServerRegistry]] = defaultdict(list)
    for row in rows:
        grouped[row.registry_source].append(row)

    sources: List[SourceStats] = []

    for source, items in grouped.items():
        total = len(items)
        scanned_7d = sum(
            1
            for i in items
            if i.last_scanned is not None and i.last_scanned >= seven_days_ago
        )
        scanned_30d = sum(
            1
            for i in items
            if i.last_scanned is not None and i.last_scanned >= thirty_days_ago
        )
        never = sum(1 for i in items if i.last_scanned is None)

        # Average days since scan (only for rows that have a scan)
        days_since = [
            (now - i.last_scanned).days
            for i in items
            if i.last_scanned is not None
        ]
        avg_days = round(sum(days_since) / len(days_since), 2) if days_since else None

        tier_dist: Dict[str, int] = defaultdict(int)
        for i in items:
            tier = i.risk_tier or "unknown"
            tier_dist[tier] += 1

        sources.append(
            SourceStats(
                source=source,
                total_servers=total,
                scanned_last_7d=scanned_7d,
                scanned_last_30d=scanned_30d,
                never_scanned=never,
                avg_days_since_scan=avg_days,
                tier_distribution=dict(tier_dist),
            )
        )

    return FreshnessResponse(sources=sources)


# ----------------------------------------------------------------------
# FastAPI app (used only for the self‑test)
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (run with: python -m services.staged.server_freshness_by_source.contract)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # In‑memory SQLite setup (overrides the real DB for testing only)
    # ------------------------------------------------------------------
    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=ENGINE)

    # Create tables
    Base.metadata.create_all(bind=ENGINE)

    # Dependency override
    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    now = datetime.datetime.utcnow()
    test_rows = [
        # source A (4 servers)
        McpServerRegistry(
            server_id="a1",
            registry_source="sourceA",
            last_scanned=now,
            scan_count=1,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="a2",
            registry_source="sourceA",
            last_scanned=now - datetime.timedelta(days=5),
            scan_count=2,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="a3",
            registry_source="sourceA",
            last_scanned=now - datetime.timedelta(days=10),
            scan_count=3,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="a4",
            registry_source="sourceA",
            last_scanned=None,
            scan_count=0,
            risk_tier="low",
        ),
        # source B (3 servers)
        McpServerRegistry(
            server_id="b1",
            registry_source="sourceB",
            last_scanned=now - datetime.timedelta(days=2),
            scan_count=1,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="b2",
            registry_source="sourceB",
            last_scanned=now - datetime.timedelta(days=20),
            scan_count=2,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="b3",
            registry_source="sourceB",
            last_scanned=None,
            scan_count=0,
            risk_tier="low",
        ),
        # source C (3 servers)
        McpServerRegistry(
            server_id="c1",
            registry_source="sourceC",
            last_scanned=now - datetime.timedelta(days=1),
            scan_count=1,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="c2",
            registry_source="sourceC",
            last_scanned=now - datetime.timedelta(days=15),
            scan_count=2,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="c3",
            registry_source="sourceC",
            last_scanned=None,
            scan_count=0,
            risk_tier="high",
        ),
    ]

    with SessionLocal() as sess:
        sess.add_all(test_rows)
        sess.commit()

    # ------------------------------------------------------------------
    # Run test client
    # ------------------------------------------------------------------
    client = TestClient(app)

    resp = client.get("/api/registry/freshness-by-source")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "sources" in data, "Missing 'sources' key"
    assert len(data["sources"]) == 3, f"Expected 3 sources, got {len(data['sources'])}"

    # Find sourceA stats
    src_a = next(s for s in data["sources"] if s["registry_source"] == "sourceA")
    assert src_a["total_servers"] == 4
    assert src_a["scanned_last_7d"] == 2
    assert src_a["scanned_last_30d"] == 3
    assert src_a["never_scanned"] == 1
    # avg_days_since_scan should be (0+5+10)/3 = 5.0
    assert abs(src_a["avg_days_since_scan"] - 5.0) < 0.01
    assert src_a["tier_distribution"] == {"high": 1, "medium": 1, "low": 2}

    print("PASS")
    sys.exit(0)