from typing import Dict

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class AxisScore(BaseModel):
    label: str
    p_top: float


class VerdictViewResponse(BaseModel):
    server_id: int
    verdict: str
    risk_tier: str
    scores: Dict[str, AxisScore]


async def get_verdict_view(
    server_id: int,
    db=Depends(get_session),
) -> VerdictViewResponse:
    """Return verdict view for a given server."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    score_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    scores: Dict[str, AxisScore] = {}
    for row in score_rows:
        scores[row.axis] = AxisScore(label=row.label, p_top=row.p_top)

    return VerdictViewResponse(
        server_id=server.server_id,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        scores=scores,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import Base to create tables in the in‑memory SQLite DB
    from app.models import Base  # type: ignore

    async def _self_test():
        # Set up in‑memory SQLite DB
        engine = create_engine("sqlite:///:memory:", echo=False, future=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(engine)

        db = SessionLocal()

        # Insert a test server and a single axis score
        test_server = McpServerRegistry(
            server_id=1,
            verdict="allow",
            risk_tier="low",
        )
        db.add(test_server)

        test_score = McpLlmAxisScore(
            server_id=1,
            axis="security",
            label="secure",
            p_top=0.92,
        )
        db.add(test_score)
        db.commit()

        # Call the logic directly
        result = await get_verdict_view(1, db=db)

        # Assertions
        assert result.server_id == 1
        assert result.verdict == "allow"
        assert result.risk_tier == "low"
        assert "security" in result.scores
        assert result.scores["security"].label == "secure"
        assert abs(result.scores["security"].p_top - 0.92) < 1e-6

        print("PASS")

    asyncio.run(_self_test())