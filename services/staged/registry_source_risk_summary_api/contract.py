# services/staged/registry_source_risk_summary_api/contract.py
"""
FastAPI contract for the Registry Source Risk Summary API.

Provides:
    GET /api/registry/sources/risk-summary
which returns a summary of server counts, risk‑tier distribution and average trust
score per registry source.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real application data layer – do NOT replace.
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class SourceSummary(BaseModel):
    """Summary for a single registry source."""
    source: str = Field(..., description="The registry source name")
    total_servers: int = Field(..., description="Number of servers for this source")
    tier_distribution: Dict[str, int] = Field(
        ..., description="Mapping of risk tier name → server count"
    )
    avg_trust_score: Optional[float] = Field(
        None,
        description="Average trust_score for the source (null if no servers)",
    )


class RegistrySourceRiskSummaryResponse(BaseModel):
    """Top‑level response model."""
    sources: List[SourceSummary] = Field(..., description="List of source summaries")


# --------------------------------------------------------------------------- #
# Core business logic
# --------------------------------------------------------------------------- #
def _compute_summary(session: Session) -> List[SourceSummary]:
    """
    Pull all rows from ``McpServerRegistry`` and aggregate them by
    ``registry_source``.
    """
    rows = (
        session.query(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
        .filter(McpServerRegistry.registry_source.isnot(None))
        .all()
    )

    # source → aggregation bucket
    agg: Dict[str, Dict] = defaultdict(
        lambda: {
            "total": 0,
            "trust_sum": 0.0,
            "tiers": defaultdict(int),
        }
    )

    for source, tier, trust in rows:
        bucket = agg[source]
        bucket["total"] += 1
        if trust is not None:
            bucket["trust_sum"] += float(trust)
        bucket["tiers"][tier] += 1

    result: List[SourceSummary] = []
    for source, data in agg.items():
        total = data["total"]
        avg = data["trust_sum"] / total if total > 0 else None
        result.append(
            SourceSummary(
                source=source,
                total_servers=total,
                tier_distribution=dict(data["tiers"]),
                avg_trust_score=avg,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# FastAPI endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/api/registry/sources/risk-summary",
    response_model=RegistrySourceRiskSummaryResponse,
    tags=["registry"],
)
def get_registry_source_risk_summary(
    session: Session = Depends(get_session),
) -> RegistrySourceRiskSummaryResponse:
    """
    Return a risk‑tier summary for each registry source.
    """
    summaries = _compute_summary(session)
    return RegistrySourceRiskSummaryResponse(sources=summaries)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.registry_source_risk_summary_api.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real schema.
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    # ------------------------------------------------------------------- #
    # Seed test data: 4 servers across two sources with known risk tiers.
    # ------------------------------------------------------------------- #
    def seed():
        sess = TestSessionLocal()
        # Minimal required fields – other columns are nullable in the real model.
        servers = [
            McpServerRegistry(
                server_id="srv-1",
                registry_source="github",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.9,
                name="srv-1",
                url="http://example.com/1",
                verdict="UNKNOWN",
            ),
            McpServerRegistry(
                server_id="srv-2",
                registry_source="github",
                risk_tier="TRUSTED_RESEARCH",
                trust_score=0.8,
                name="srv-2",
                url="http://example.com/2",
                verdict="UNKNOWN",
            ),
            McpServerRegistry(
                server_id="srv-3",
                registry_source="npm",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.7,
                name="srv-3",
                url="http://example.com/3",
                verdict="UNKNOWN",
            ),
            McpServerRegistry(
                server_id="srv-4",
                registry_source="npm",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.6,
                name="srv-4",
                url="http://example.com/4",
                verdict="UNKNOWN",
            ),
        ]
        sess.add_all(servers)
        sess.commit()
        sess.close()

    seed()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def override_get_session() -> Session:
        return TestSessionLocal()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute acceptance test.
    # ------------------------------------------------------------------- #
    resp = client.get("/api/registry/sources/risk-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert "sources" in payload, "Missing 'sources' key"
    sources = {s["source"]: s for s in payload["sources"]}

    # Both sources must be present.
    assert {"github", "npm"} <= set(sources.keys()), "Missing expected sources"

    for src, data in sources.items():
        total = data["total_servers"]
        tier_sum = sum(data["tier_distribution"].values())
        assert tier_sum == total, f"Tier distribution mismatch for {src}"
        # avg_trust_score should be a float (or null) – basic sanity check.
        if total > 0:
            assert isinstance(data["avg_trust_score"], (float, int)), (
                f"Invalid avg_trust_score for {src}"
            )

    print("PASS")
    sys.exit(0)