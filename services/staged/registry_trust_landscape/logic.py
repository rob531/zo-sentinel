# services/staged/registry_trust_landscape/logic.py
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/registry/trust-landscape", tags=["registry_trust_landscape"])


class TierDistribution(BaseModel):
    TRUSTED_GENERAL: int = 0
    TRUSTED_RESEARCH: int = 0
    UNTRUSTED: int = 0
    UNKNOWN: int = 0


class SourceSummary(BaseModel):
    registry_source: str
    server_count: int
    avg_risk_score: float = Field(..., description="Average overall_risk score")
    tier_distribution: TierDistribution
    signal_coverage_pct: float = Field(..., description="Pct of servers with >=5 axes scored")


class TrustLandscapeResponse(BaseModel):
    generated_at: datetime
    sources: List[SourceSummary]


def _aggregate_source(session: Session) -> List[SourceSummary]:
    # Subquery: map each server to its registry source
    srv_src = (
        session.query(
            McpServerRegistry.server_id.label("server_id"),
            McpServerRegistry.registry_source.label("registry_source"),
        )
        .subquery()
    )

    # Per‑server aggregates: axis count and overall_risk score
    server_agg = (
        session.query(
            srv_src.c.registry_source,
            srv_src.c.server_id,
            func.count(McpLlmAxisScore.id).label("axis_cnt"),
            func.avg(
                case(
                    [(McpLlmAxisScore.axis_name == "overall_risk", McpLlmAxisScore.score)],
                    else_=None,
                )
            ).label("overall_score"),
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == srv_src.c.server_id,
        )
        .group_by(srv_src.c.registry_source, srv_src.c.server_id)
        .subquery()
    )

    # Per‑source summary (server count, avg risk, signal coverage)
    source_summary_q = (
        session.query(
            server_agg.c.registry_source,
            func.count().label("server_count"),
            func.avg(server_agg.c.overall_score).label("avg_risk_score"),
            (
                func.sum(
                    case([(server_agg.c.axis_cnt >= 5, 1)], else_=0)
                )
                / func.count()
                * 100
            ).label("signal_coverage_pct"),
        )
        .group_by(server_agg.c.registry_source)
        .all()
    )

    # Tier distribution per source
    tier_q = (
        session.query(
            srv_src.c.registry_source,
            McpLlmAxisScore.risk_tier,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("cnt"),
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == srv_src.c.server_id,
        )
        .group_by(srv_src.c.registry_source, McpLlmAxisScore.risk_tier)
        .all()
    )

    # Build dict: source -> tier -> count
    tier_map: Dict[str, Dict[str, int]] = {}
    for src, tier, cnt in tier_q:
        tier_map.setdefault(src, {})[tier] = cnt

    results: List[SourceSummary] = []
    for src, srv_cnt, avg_score, coverage in source_summary_q:
        tiers = tier_map.get(src, {})
        dist = TierDistribution(
            TRUSTED_GENERAL=tiers.get("TRUSTED_GENERAL", 0),
            TRUSTED_RESEARCH=tiers.get("TRUSTED_RESEARCH", 0),
            UNTRUSTED=tiers.get("UNTRUSTED", 0),
            UNKNOWN=tiers.get("UNKNOWN", 0),
        )
        results.append(
            SourceSummary(
                registry_source=src,
                server_count=srv_cnt,
                avg_risk_score=float(avg_score) if avg_score is not None else 0.0,
                tier_distribution=dist,
                signal_coverage_pct=float(coverage) if coverage is not None else 0.0,
            )
        )
    return results


@router.get("/", response_model=TrustLandscapeResponse)
def get_trust_landscape(session: Session = Depends(get_session)):
    """Return the trust‑landscape view grouped by registry source."""
    sources = _aggregate_source(session)
    return TrustLandscapeResponse(generated_at=datetime.utcnow(), sources=sources)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for test – overrides the real DB dependency
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Seed data
    with TestSession() as s:
        # Registry sources
        s.add_all(
            [
                McpServerRegistry(server_id=1, registry_source="source_A"),
                McpServerRegistry(server_id=2, registry_source="source_A"),
                McpServerRegistry(server_id=3, registry_source="source_A"),
                McpServerRegistry(server_id=4, registry_source="source_B"),
                McpServerRegistry(server_id=5, registry_source="source_B"),
                McpServerRegistry(server_id=6, registry_source="source_B"),
            ]
        )
        # Axis scores (overall_risk + other axes)
        scores = [
            # source_A servers
            McpLlmAxisScore(
                server_id=1,
                axis_name="overall_risk",
                score=2.0,
                risk_tier="TRUSTED_GENERAL",
            ),
            McpLlmAxisScore(server_id=1, axis_name="axis1", score=1.0, risk_tier="TRUSTED_GENERAL"),
            McpLlmAxisScore(server_id=1, axis_name="axis2", score=1.0, risk_tier="TRUSTED_GENERAL"),
            McpLlmAxisScore(server_id=1, axis_name="axis3", score=1.0, risk_tier="TRUSTED_GENERAL"),
            McpLlmAxisScore(server_id=1, axis_name="axis4", score=1.0, risk_tier="TRUSTED_GENERAL"),
            # server 2 – fewer axes
            McpLlmAxisScore(server_id=2, axis_name="overall_risk", score=4.0, risk_tier="UNTRUSTED"),
            # server 3 – enough axes, different tier
            McpLlmAxisScore(server_id=3, axis_name="overall_risk", score=3.0, risk_tier="TRUSTED_RESEARCH"),
            McpLlmAxisScore(server_id=3, axis_name="axis1", score=1.0, risk_tier="TRUSTED_RESEARCH"),
            McpLlmAxisScore(server_id=3, axis_name="axis2", score=1.0, risk_tier="TRUSTED_RESEARCH"),
            McpLlmAxisScore(server_id=3, axis_name="axis3", score=1.0, risk_tier="TRUSTED_RESEARCH"),
            McpLlmAxisScore(server_id=3, axis_name="axis4", score=1.0, risk_tier="TRUSTED_RESEARCH"),
            # source_B servers
            McpLlmAxisScore(server_id=4, axis_name="overall_risk", score=5.0, risk_tier="UNTRUSTED"),
            McpLlmAxisScore(server_id=4, axis_name="axis1", score=1.0, risk_tier="UNTRUSTED"),
            McpLlmAxisScore(server_id=4, axis_name="axis2", score=1.0, risk_tier="UNTRUSTED"),
            McpLlmAxisScore(server_id=4, axis_name="axis3", score=1.0, risk_tier="UNTRUSTED"),
            McpLlmAxisScore(server_id=4, axis_name="axis4", score=1.0, risk_tier="UNTRUSTED"),
            McpLlmAxisScore(server_id=5, axis_name="overall_risk", score=1.0, risk_tier="TRUSTED_GENERAL"),
            McpLlmAxisScore(server_id=6, axis_name="overall_risk", score=2.5, risk_tier="UNKNOWN"),
        ]
        s.add_all(scores)
        s.commit()

        # Override dependency for direct call
        result = _aggregate_source(s)

        # Basic assertions
        assert isinstance(result, list) and len(result) == 2, "should have two sources"
        for src in result:
            assert isinstance(src.avg_risk_score, float), "avg_risk_score must be float"
            td = src.tier_distribution
            # Ensure all expected keys exist
            for key in ("TRUSTED_GENERAL", "TRUSTED_RESEARCH", "UNTRUSTED", "UNKNOWN"):
                assert hasattr(td, key), f"tier_distribution missing {key}"
        print("PASS")