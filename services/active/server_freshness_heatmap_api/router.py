# services/active/server_freshness_heatmap_api/router.py
"""
Server freshness heatmap API -- aggregates server scan-age into buckets
(recent / stale_gt_7d / stale_gt_30d / never_scored) broken down by
registry source.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_freshness_heatmap_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class FreshnessBucket(BaseModel):
    label: str
    count: int
    servers: List[str]


class SourceStats(BaseModel):
    source: str
    count: int
    avg_age_hours: float


class HeatmapResponse(BaseModel):
    generated_at: str
    buckets: List[FreshnessBucket]
    source_breakdown: List[SourceStats]


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

def compute_freshness_buckets(session: Session) -> Dict[str, Any]:
    """Compute freshness buckets from McpServerRegistry using last_scanned."""
    now = datetime.utcnow()
    servers = session.query(McpServerRegistry).all()

    never_scored: List[str] = []
    stale_gt_7d: List[str] = []
    stale_gt_30d: List[str] = []
    recent: List[str] = []
    source_stats: Dict[str, Dict[str, Any]] = {}

    for srv in servers:
        sid = srv.server_id
        if srv.last_scanned is None:
            never_scored.append(sid)
        else:
            age: timedelta = now - srv.last_scanned
            age_days = age.days
            if age_days > 30:
                stale_gt_30d.append(sid)
            elif age_days > 7:
                stale_gt_7d.append(sid)
            else:
                recent.append(sid)

        source = srv.registry_source or "unknown"
        if source not in source_stats:
            source_stats[source] = {"total": 0, "total_age_hours": 0.0}
        source_stats[source]["total"] += 1
        if srv.last_scanned is not None:
            source_stats[source]["total_age_hours"] += (
                now - srv.last_scanned
            ).total_seconds() / 3600

    buckets = [
        {"label": "recent", "count": len(recent), "servers": recent},
        {"label": "stale_gt_7d", "count": len(stale_gt_7d), "servers": stale_gt_7d},
        {"label": "stale_gt_30d", "count": len(stale_gt_30d), "servers": stale_gt_30d},
        {"label": "never_scored", "count": len(never_scored), "servers": never_scored},
    ]

    source_breakdown = []
    for src, stats in source_stats.items():
        avg_age = (
            stats["total_age_hours"] / stats["total"]
            if stats["total"] > 0
            else 0.0
        )
        source_breakdown.append(
            {"source": src, "count": stats["total"], "avg_age_hours": round(avg_age, 2)}
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "buckets": buckets,
        "source_breakdown": source_breakdown,
    }


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/registry/freshness-heatmap",
    response_model=HeatmapResponse,
    summary="Server freshness heatmap by age bucket and source",
)
def get_freshness_heatmap(
    session: Session = Depends(get_session),
) -> HeatmapResponse:
    """
    Return a heatmap of server freshness: count-per-bucket (recent,
    stale_gt_7d, stale_gt_30d, never_scored) and a per-source breakdown
    of average scan age.
    """
    data = compute_freshness_buckets(session)
    return HeatmapResponse(**data)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory SQLite bound to real SQLAlchemy models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    # Build a LOCAL FastAPI app (not app.main) with dependency override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Seed test data
    now = datetime.utcnow()
    with TestingSession() as db:
        db.add(McpServerRegistry(
            server_id="srv-001", registry_source="vendor_a",
            last_scanned=now - timedelta(hours=2),
        ))
        db.add(McpServerRegistry(
            server_id="srv-002", registry_source="vendor_b",
            last_scanned=None,
        ))
        db.add(McpServerRegistry(
            server_id="srv-003", registry_source="vendor_a",
            last_scanned=now - timedelta(days=35),
        ))
        db.add(McpServerRegistry(
            server_id="srv-004", registry_source="community",
            last_scanned=now - timedelta(days=10),
        ))
        db.add(McpServerRegistry(
            server_id="srv-005", registry_source="community",
            last_scanned=None,
        ))
        db.commit()

    # Happy-path assertion
    resp = client.get("/api/registry/freshness-heatmap")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    assert "buckets" in data
    buckets_by_label = {b["label"]: b for b in data["buckets"]}
    assert len(buckets_by_label) >= 3

    assert buckets_by_label["recent"]["count"] == 1
    assert buckets_by_label["stale_gt_7d"]["count"] == 1
    assert buckets_by_label["stale_gt_30d"]["count"] == 1
    assert buckets_by_label["never_scored"]["count"] == 2

    assert "source_breakdown" in data
    sources = {s["source"]: s for s in data["source_breakdown"]}
    assert sources["vendor_a"]["count"] == 2
    assert sources["vendor_b"]["count"] == 1
    assert sources["community"]["count"] == 2

    print("PASS")
