"""logic.py -- trust tier divergence computation over the real app data layer."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _band_for_probability(p_top: Optional[float]) -> str:
    """Return the risk band represented by an overall-risk probability."""
    if p_top is None:
        return "unknown"
    if p_top >= 0.75:
        return "low_risk"
    if p_top >= 0.45:
        return "medium_risk"
    return "high_risk"


def _band_for_trust_score(trust_score: Optional[float]) -> str:
    """Return the risk band represented by the legacy trust score."""
    if trust_score is None:
        return "unknown"
    if trust_score >= 75:
        return "low_risk"
    if trust_score >= 45:
        return "medium_risk"
    return "high_risk"


def get_trust_tier_divergence(
    db: Session,
    min_delta: float = 40,
    limit: int = 100,
) -> Dict[str, Any]:
    """Find registry trust scores that disagree with the latest LLM risk score.

    The latest ``overall_risk`` row is selected for every server.  A server is
    divergent when its registry risk tier is not insufficient and its trust
    score falls in a different band from the LLM probability, or when the
    absolute difference between the trust score and ``p_top * 100`` exceeds
    ``min_delta``.
    """
    latest_rows = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.scored_at.label("scored_at"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
    ).subquery()

    rows = db.execute(
        select(
            latest_rows.c.server_id,
            McpServerRegistry.name,
            McpServerRegistry.trust_score,
            latest_rows.c.p_top,
            McpServerRegistry.risk_tier,
            latest_rows.c.scored_at,
            latest_rows.c.decision_rule_version,
        )
        .select_from(
            latest_rows.join(
                McpServerRegistry,
                McpServerRegistry.server_id == latest_rows.c.server_id,
            )
        )
        .where(latest_rows.c.row_number == 1)
    ).all()

    divergences: List[Dict[str, Any]] = []
    checked = 0

    for row in rows:
        checked += 1
        trust_score = row.trust_score
        p_top = row.p_top

        if p_top is None or trust_score is None:
            continue

        expected_band = _band_for_probability(float(p_top))
        trust_band = _band_for_trust_score(float(trust_score))
        registry_tier = (row.risk_tier or "").upper()
        tier_disagreement = registry_tier != "INSUFFICIENT" and trust_band != expected_band
        delta_points = abs(float(trust_score) - float(p_top) * 100.0)

        if not (tier_disagreement or delta_points > float(min_delta)):
            continue

        divergences.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "trust_score": trust_score,
                "p_top": p_top,
                "registry_risk_tier": row.risk_tier,
                "expected_band": expected_band,
                "delta_points": delta_points,
                "scored_at": row.scored_at,
                "decision_rule_version": row.decision_rule_version,
            }
        )

    divergences.sort(key=lambda item: item["delta_points"], reverse=True)
    if limit is not None:
        divergences = divergences[: max(0, int(limit))]

    return {
        "checked": checked,
        "divergent": len(divergences),
        "divergences": divergences,
    }


def find_trust_tier_divergences(
    db: Session,
    min_delta: float = 40,
    limit: int = 100,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the pluralized operation name."""
    return get_trust_tier_divergence(db, min_delta=min_delta, limit=limit)


def get_divergence_report(
    db: Session,
    min_delta: float = 40,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return the trust-tier divergence report."""
    return get_trust_tier_divergence(db, min_delta=min_delta, limit=limit)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="matching",
                name="matching",
                trust_score=90,
                risk_tier="LOW_RISK",
            ),
            McpServerRegistry(
                server_id="high-risk-score",
                name="high-risk-score",
                trust_score=90,
                risk_tier="LOW_RISK",
            ),
            McpServerRegistry(
                server_id="low-trust-score",
                name="low-trust-score",
                trust_score=20,
                risk_tier="LOW_RISK",
            ),
        ]
    )
    test_db.flush()
    test_db.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="matching",
                axis_name="overall_risk",
                p_top=0.9,
                model_version="test-model",
                decision_rule_version="test-rule",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="high-risk-score",
                axis_name="overall_risk",
                p_top=0.2,
                model_version="test-model",
                decision_rule_version="test-rule",
            ),
            McpLlmAxisScore(
                id=3,
                server_id="low-trust-score",
                axis_name="overall_risk",
                p_top=0.9,
                model_version="test-model",
                decision_rule_version="test-rule",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scores/trust-tier-divergence")
    def trust_tier_divergence_endpoint(db: Session = Depends(get_session)):
        return get_trust_tier_divergence(db)

    def override_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_session
    with TestClient(test_app) as client:
        response = client.get("/api/scores/trust-tier-divergence")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["checked"] == 3, payload
        assert payload["divergent"] == 2, payload
        listed = {item["server_id"] for item in payload["divergences"]}
        assert listed == {"high-risk-score", "low-trust-score"}, payload
        assert "matching" not in listed, payload

    test_db.close()
    test_engine.dispose()
    print("PASS")