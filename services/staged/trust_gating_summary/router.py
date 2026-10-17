from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_trust_gating_summary(db: Session) -> dict:
    results = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers = []
    for row in results:
        risk_tier = row.risk_tier
        count = row.count

        top_servers = (
            db.query(McpServerRegistry.name)
            .join(
                McpLlmAxisScore,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .filter(
                McpLlmAxisScore.axis_name == "overall_risk",
                McpServerRegistry.risk_tier == risk_tier,
            )
            .order_by(desc(McpLlmAxisScore.p_top))
            .limit(1)
            .all()
        )

        example_servers = [s.name for s in top_servers]

        tiers.append({
            "risk_tier": risk_tier,
            "count": count,
            "example_servers": example_servers,
        })

    return {"tiers": tiers}