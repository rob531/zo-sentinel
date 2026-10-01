# deps: fastapi, pydantic, sqlalchemy
"""Registry Health Dashboard API.

GET /api/registry/health-dashboard
  Returns per-source health metrics for all servers in mcp_server_registry:
  server count, avg scan count, first_seen oldest, last_scanned newest,
  and risk-tier breakdown per source.

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

# Ensure repo root is on sys.path so `app.db` resolves when run directly
_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry_health_dashboard_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class SourceHealthStats(BaseModel):
    source: str = Field(..., description="Registry source name")
    server_count: int = Field(..., description="Total servers from this source")
    avg_scan_count: float = Field(..., description="Average scan count")
    first_seen_oldest: datetime | None = Field(
        None, description="Oldest first_seen timestamp"
    )
    last_scanned_newest: datetime | None = Field(
        None, description="Most recent last_scanned timestamp"
    )
    tier_breakdown: dict[str, int] = Field(
        default_factory=dict, description="Count of servers per risk tier"
    )

    model_config = {"from_attributes": True}


class RegistryHealthDashboardResponse(BaseModel):
    sources: list[SourceHealthStats] = Field(
        default_factory=list, description="Health stats per registry source"
    )


# --------------------------------------------------------------------------- #
# Business logic
# --------------------------------------------------------------------------- #

def compute_source_health(db: Session) -> list[SourceHealthStats]:
    """Aggregate health metrics grouped by registry_source."""
    rows = (
        db.query(
            McpServerRegistry.registry_source,
            McpServerRegistry.server_id,
            McpServerRegistry.scan_count,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.risk_tier,
        )
        .filter(McpServerRegistry.registry_source.isnot(None))
        .all()
    )

    # Group by source
    by_source: dict[str, list] = {}
    for r in rows:
        by_source.setdefault(r.registry_source, []).append(r)

    results: list[SourceHealthStats] = []
    for source, records in by_source.items():
        server_count = len(records)
        total_scans = sum(r.scan_count or 0 for r in records)
        avg_scan_count = total_scans / server_count

        oldest_seen = min(
            (r.first_seen for r in records if r.first_seen),
            default=None,
        )
        newest_scanned = max(
            (r.last_scanned for r in records if r.last_scanned),
            default=None,
        )

        tier_counts: dict[str, int] = {}
        for r in records:
            tier = r.risk_tier or "UNKNOWN"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        results.append(
            SourceHealthStats(
                source=source,
                server_count=server_count,
                avg_scan_count=round(avg_scan_count, 4),
                first_seen_oldest=oldest_seen,
                last_scanned_newest=newest_scanned,
                tier_breakdown=tier_counts,
            )
        )

    return results


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/registry/health-dashboard",
    response_model=RegistryHealthDashboardResponse,
    summary="Registry health dashboard — per-source health breakdown",
)
def registry_health_dashboard(
    db: Session = Depends(get_session),
) -> RegistryHealthDashboardResponse:
    """Return aggregated health metrics broken down by registry source."""
    sources = compute_source_health(db)
    return RegistryHealthDashboardResponse(sources=sources)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite via StaticPool (NOT imported from app.db)
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Seed 3 sources with varied tiers and scan counts
    now = datetime.now(timezone.utc)
    seed = [
        # source_a — 2 servers: LOW and MEDIUM tiers
        McpServerRegistry(
            server_id="sa-001", name="Srv A1", registry_source="source_a",
            risk_tier="LOW", scan_count=5, last_scanned=now,
            first_seen=now, url="http://a1",
        ),
        McpServerRegistry(
            server_id="sa-002", name="Srv A2", registry_source="source_a",
            risk_tier="MEDIUM", scan_count=3, last_scanned=now,
            first_seen=now, url="http://a2",
        ),
        # source_b — 2 servers: HIGH and LOW (one unscanned)
        McpServerRegistry(
            server_id="sb-001", name="Srv B1", registry_source="source_b",
            risk_tier="HIGH", scan_count=10, last_scanned=now,
            first_seen=now, url="http://b1",
        ),
        McpServerRegistry(
            server_id="sb-002", name="Srv B2", registry_source="source_b",
            risk_tier="LOW", scan_count=0, last_scanned=None,
            first_seen=now, url="http://b2",
        ),
        # source_c — 1 server: CRITICAL tier
        McpServerRegistry(
            server_id="sc-001", name="Srv C1", registry_source="source_c",
            risk_tier="CRITICAL", scan_count=20, last_scanned=now,
            first_seen=now, url="http://c1",
        ),
    ]

    with TestSession() as sess:
        for s in seed:
            sess.add(s)
        sess.commit()

    def _override() -> Generator[Session, None, None]:
        with TestSession() as s:
            yield s

    # Override get_session using the FastAPI app instance (NOT app.db)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)
    resp = client.get("/api/registry/health-dashboard")

    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    sources = data.get("sources", [])

    source_names = {s["source"] for s in sources}
    if source_names != {"source_a", "source_b", "source_c"}:
        print(f"FAIL: expected 3 sources, got {source_names}")
        sys.exit(1)

    by_name = {s["source"]: s for s in sources}

    if by_name["source_a"]["server_count"] != 2:
        print(f"FAIL: source_a count expected 2, got {by_name['source_a']['server_count']}")
        sys.exit(1)
    if by_name["source_b"]["server_count"] != 2:
        print(f"FAIL: source_b count expected 2, got {by_name['source_b']['server_count']}")
        sys.exit(1)
    if by_name["source_c"]["server_count"] != 1:
        print(f"FAIL: source_c count expected 1, got {by_name['source_c']['server_count']}")
        sys.exit(1)

    if "LOW" not in by_name["source_a"]["tier_breakdown"]:
        print(f"FAIL: source_a tier_breakdown missing LOW: {by_name['source_a']['tier_breakdown']}")
        sys.exit(1)
    if "HIGH" not in by_name["source_b"]["tier_breakdown"]:
        print(f"FAIL: source_b tier_breakdown missing HIGH: {by_name['source_b']['tier_breakdown']}")
        sys.exit(1)
    if "CRITICAL" not in by_name["source_c"]["tier_breakdown"]:
        print(f"FAIL: source_c tier_breakdown missing CRITICAL: {by_name['source_c']['tier_breakdown']}")
        sys.exit(1)

    # source_c has avg_scan_count = 20.0 exactly
    if by_name["source_c"]["avg_scan_count"] != 20.0:
        print(f"FAIL: source_c avg_scan_count expected 20.0, got {by_name['source_c']['avg_scan_count']}")
        sys.exit(1)

    print("PASS")
