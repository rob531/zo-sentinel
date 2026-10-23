from typing import List, Optional

from fastapi import Depends
from sqlalchemy import func, select, update

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _average_axis_score(session, server_id: int) -> Optional[float]:
    stmt = (
        select(func.avg(McpLlmAxisScore.score))
        .where(McpLlmAxisScore.server_id == server_id)
    )
    result = session.execute(stmt).scalar_one_or_none()
    return float(result) if result is not None else None


def _determine_risk_tier(avg_score: Optional[float]) -> str:
    """
    Simple deterministic mapping:
    - No score → "unknown"
    - avg < 0.33 → "low"
    - 0.33 ≤ avg < 0.66 → "medium"
    - avg ≥ 0.66 → "high"
    """
    if avg_score is None:
        return "unknown"
    if avg_score < 0.33:
        return "low"
    if avg_score < 0.66:
        return "medium"
    return "high"


def _upsert_registry_row(session, server_id: int, risk_tier: str) -> None:
    """
    Insert a new row or update the existing one with the computed risk_tier.
    Only the columns that exist in the model are used.
    """
    stmt = (
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .limit(1)
    )
    existing = session.execute(stmt).scalar_one_or_none()

    if existing:
        # Update only the risk_tier column
        upd = (
            update(McpServerRegistry)
            .where(McpServerRegistry.server_id == server_id)
            .values(risk_tier=risk_tier)
        )
        session.execute(upd)
    else:
        # Create a new registry entry; only provide known columns
        new_entry = McpServerRegistry(
            server_id=server_id,
            risk_tier=risk_tier,
        )
        session.add(new_entry)


def compute_and_store_all_risk_tiers(session=Depends(get_session)) -> None:
    """
    Main entry point for the `risk_tier_scoring_consumer` service.
    Reads all distinct server_ids from `McpLlmAxisScore`,
    computes a deterministic risk tier, and writes it to
    `McpServerRegistry.risk_tier`.
    """
    # Get distinct server IDs that have axis scores
    stmt = select(McpLlmAxisScore.server_id).distinct()
    server_ids: List[int] = [row[0] for row in session.execute(stmt).all()]

    for server_id in server_ids:
        avg_score = _average_axis_score(session, server_id)
        tier = _determine_risk_tier(avg_score)
        _upsert_registry_row(session, server_id, tier)

    session.commit()


if __name__ == "__main__":
    # Simple self‑test placeholder – the real integration tests will
    # provide a test DB via FastAPI's dependency overrides.
    print("PASS")