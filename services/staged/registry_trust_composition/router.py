from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session, joinedload

from app.db import get_session

router = APIRouter(prefix="/api", tags=["trust"])


class LabelDistribution(BaseModel):
    label: str
    count: int


class TierBreakdown(BaseModel):
    tier: str
    count: int
    label_distribution: dict[str, dict[str, int]]


class TrustCompositionResponse(BaseModel):
    tiers: list[dict]
    total_servers: int
    scanned_at: str


RISK_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "INSUFFICIENT",
]

AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


@router.get("/trust/composition", response_model=TrustCompositionResponse)
def get_trust_composition(db: Session = Depends(get_session)) -> TrustCompositionResponse:
    """
    Returns trust composition breakdown by risk tier.
    """
    tiers_result = []

    for tier in RISK_TIERS:
        count_query = (
            select(func.count())
            .select_from(text("mcp_server_registry"))
            .where(text("mcp_server_registry.risk_tier = :tier"))
        )
        count_result = db.execute(count_query, {"tier": tier}).scalar()
        tier_count = count_result or 0

        label_distribution = {}
        for axis in AXES:
            axis_query = (
                select(
                    text("mcp_llm_axis_scores.axis_label"),
                    func.count().label("count"),
                )
                .select_from(text("mcp_llm_axis_scores"))
                .join(
                    text("mcp_server_registry"),
                    text("mcp_server_registry.server_id = mcp_llm_axis_scores.server_id"),
                )
                .where(
                    text("mcp_server_registry.risk_tier = :tier")
                )
                .where(text("mcp_llm_axis_scores.axis_name = :axis"))
                .group_by(text("mcp_llm_axis_scores.axis_label"))
            )
            axis_results = db.execute(axis_query, {"tier": tier, "axis": axis}).all()
            label_distribution[axis] = {row.axis_label: row.count for row in axis_results}

        tiers_result.append({
            "tier": tier,
            "count": tier_count,
            "label_distribution": label_distribution,
        })

    total_query = select(func.count()).select_from(text("mcp_server_registry"))
    total_servers = db.execute(total_query).scalar() or 0

    return TrustCompositionResponse(
        tiers=tiers_result,
        total_servers=total_servers,
        scanned_at=datetime.utcnow().isoformat(),
    )


if __name__ == "__main__":
    from sqlalchemy import Column, Integer, String, ForeignKey
    from sqlalchemy.orm import declarative_base
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:", echo=False)

    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id INTEGER PRIMARY KEY, risk_tier TEXT)"))
        conn.execute(text("""CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY,
            server_id INTEGER,
            axis_name TEXT,
            axis_label TEXT
        )"""))
        conn.commit()

    from sqlalchemy.orm import Session as SqlSession

    with SqlSession(test_engine) as db:
        for i, tier in enumerate(RISK_TIERS):
            db.execute(text("INSERT INTO mcp_server_registry (server_id, risk_tier) VALUES (:sid, :tier)"),
                      {"sid": i + 1, "tier": tier})

        axis_labels = ["low", "medium", "high", "critical"]
        for sid in range(1, 7):
            for ax_idx, axis in enumerate(AXES):
                label = axis_labels[(sid + ax_idx) % len(axis_labels)]
                db.execute(
                    text("INSERT INTO mcp_llm_axis_scores (server_id, axis_name, axis_label) VALUES (:sid, :axis, :label)"),
                    {"sid": sid, "axis": axis, "label": label}
                )
        db.commit()

    with SqlSession(test_engine) as db:
        result = get_trust_composition(db=db)

    assert result.total_servers == 6, f"Expected total_servers=6, got {result.total_servers}"
    assert len(result.tiers) == 6, f"Expected 6 tier entries, got {len(result.tiers)}"

    tiers_with_data = {t["tier"] for t in result.tiers if t["count"] > 0}
    assert len(tiers_with_data) == 6, f"Expected all 6 tiers present, got {tiers_with_data}"

    for tier_data in result.tiers:
        if tier_data["count"] > 0:
            assert set(tier_data["label_distribution"].keys()) == set(AXES), \
                f"Tier {tier_data['tier']} missing axes. Expected {set(AXES)}, got {set(tier_data['label_distribution'].keys())}"

    print("PASS")