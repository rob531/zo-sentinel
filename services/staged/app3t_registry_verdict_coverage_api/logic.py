"""logic.py -- data/computation layer for app3t_registry_verdict_coverage_api.

Provides a single pure function `verdict_coverage` that aggregates
`McpServerRegistry` rows into a coverage report.  The router (in the same
package) calls this function and returns a Pydantic model.
"""

from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

# ---------------------------------------------------------------------------


def _median(values: List[float]) -> Optional[float]:
    """Return the median of a list of numbers or None if empty."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def verdict_coverage(db: Session) -> Dict:
    """
    Compute coverage roll‑ups per ``registry_source`` from ``McpServerRegistry``.

    Returns a dict matching the response model used by the router.
    """
    now = datetime.datetime.utcnow()
    stale_threshold = now - datetime.timedelta(days=30)

    # Load all rows – the table is small enough for the service's use‑case.
    rows = db.execute(select(McpServerRegistry)).scalars().all()
    total_servers = len(rows)

    # Group rows by source.
    source_groups: Dict[Optional[str], List[McpServerRegistry]] = {}
    for row in rows:
        src = row.registry_source
        source_groups.setdefault(src, []).append(row)

    by_source: List[Dict] = []
    worst_source_name: Optional[str] = None
    worst_coverage = 101.0  # start above max possible

    for src, items in source_groups.items():
        total = len(items)
        with_verdict = sum(1 for i in items if i.verdict is not None)
        without_verdict = total - with_verdict
        coverage_pct = round((with_verdict / total) * 100, 2) if total else 0.0

        # Verdict breakdown (skip null verdicts).
        verdict_breakdown: Dict[str, int] = {}
        for i in items:
            if i.verdict is not None:
                verdict_breakdown[str(i.verdict)] = verdict_breakdown.get(
                    str(i.verdict), 0
                ) + 1

        # Risk tier breakdown (skip null tiers).
        risk_tier_breakdown: Dict[str, int] = {}
        for i in items:
            if i.risk_tier is not None:
                risk_tier_breakdown[str(i.risk_tier)] = risk_tier_breakdown.get(
                    str(i.risk_tier), 0
                ) + 1

        # Stale count.
        stale_count = sum(
            1
            for i in items
            if i.last_assessed is not None and i.last_assessed < stale_threshold
        )

        # Trust‑score statistics.
        trust_scores = [float(i.trust_score) for i in items if i.trust_score is not None]
        median_trust_score = _median(trust_scores)

        # Average scan count.
        scan_counts = [i.scan_count for i in items if i.scan_count is not None]
        avg_scan_count = (
            sum(scan_counts) / len(scan_counts) if scan_counts else None
        )

        src_dict: Dict = {
            "registry_source": src,
            "total": total,
            "with_verdict": with_verdict,
            "without_verdict": without_verdict,
            "coverage_pct": coverage_pct,
            "verdict_breakdown": verdict_breakdown,
            "risk_tier_breakdown": risk_tier_breakdown,
            "stale_count": stale_count,
            "median_trust_score": median_trust_score,
            "avg_scan_count": avg_scan_count,
        }
        by_source.append(src_dict)

        # Track worst source (lowest coverage).
        if coverage_pct < worst_coverage:
            worst_coverage = coverage_pct
            worst_source_name = src

    # Unscored total – rows where trust_score is NULL.
    unscored_total = sum(1 for i in rows if i.trust_score is None)

    result = {
        "generated_at": now.isoformat() + "Z",
        "total_servers": total_servers,
        "by_source": by_source,
        "unscored_total": unscored_total,
        "worst_source": worst_source_name,
    }
    return result


# ---------------------------------------------------------------------------
# FastAPI router exposing the endpoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite DB.
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel, Field
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # -----------------------------------------------------------------------
    # Pydantic response models
    # -----------------------------------------------------------------------
    class SourceBreakdown(BaseModel):
        registry_source: Optional[str] = Field(None)
        total: int
        with_verdict: int
        without_verdict: int
        coverage_pct: float
        verdict_breakdown: Dict[str, int]
        risk_tier_breakdown: Dict[str, int]
        stale_count: int
        median_trust_score: Optional[float] = None
        avg_scan_count: Optional[float] = None

    class CoverageResponse(BaseModel):
        generated_at: str
        total_servers: int
        by_source: List[SourceBreakdown]
        unscored_total: int
        worst_source: Optional[str] = None

    # -----------------------------------------------------------------------
    # FastAPI app & router
    # -----------------------------------------------------------------------
    app = FastAPI()

    @app.get(
        "/api/registry/verdict-coverage",
        response_model=CoverageResponse,
        tags=["registry"],
    )
    def get_coverage(session: Session = Depends(get_session)):
        return verdict_coverage(session)

    # -----------------------------------------------------------------------
    # In‑memory DB setup for the test
    # -----------------------------------------------------------------------
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables for the models we use.
    McpServerRegistry.__table__.metadata.create_all(engine)

    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # Seed data.
    with TestSessionLocal() as sess:
        now = datetime.datetime.utcnow()
        stale_date = now - datetime.timedelta(days=31)

        # Server 1 – source A, has verdict, recent assessment.
        sess.add(
            McpServerRegistry(
                server_id="srv-1",
                registry_source="source-A",
                verdict="clean",
                risk_tier="low",
                trust_score=0.9,
                last_assessed=now,
                scan_count=5,
            )
        )
        # Server 2 – source A, no verdict, recent assessment.
        sess.add(
            McpServerRegistry(
                server_id="srv-2",
                registry_source="source-A",
                verdict=None,
                risk_tier="medium",
                trust_score=None,
                last_assessed=now,
                scan_count=3,
            )
        )
        # Server 3 – source B, has verdict, stale assessment.
        sess.add(
            McpServerRegistry(
                server_id="srv-3",
                registry_source="source-B",
                verdict="malicious",
                risk_tier="high",
                trust_score=0.2,
                last_assessed=stale_date,
                scan_count=7,
            )
        )
        sess.commit()

    # -----------------------------------------------------------------------
    # Run test client assertions
    # -----------------------------------------------------------------------
    client = TestClient(app)

    resp = client.get("/api/registry/verdict-coverage")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 3, "total_servers mismatch"
    total_by_source = sum(src["total"] for src in data["by_source"])
    assert total_by_source == 3, "by_source totals do not sum to 3"

    for src in data["by_source"]:
        cp = src["coverage_pct"]
        assert 0.0 <= cp <= 100.0, f"coverage_pct out of bounds: {cp}"

    print("PASS")