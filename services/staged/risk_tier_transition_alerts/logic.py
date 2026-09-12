import datetime
from datetime import timedelta
from typing import List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore


class ServerTierChange(BaseModel):
    server_id: int
    name: str
    old_tier: str
    new_tier: str
    changed_at: datetime.datetime


class TierChangesResponse(BaseModel):
    servers: List[ServerTierChange]


def get_risk_tier_transitions(
    session: Session = Depends(get_session),
) -> TierChangesResponse:
    """
    Return servers whose risk tier changed in the last 24 hours.
    """
    cutoff = datetime.datetime.utcnow() - timedelta(hours=24)

    # CTE with row numbers per server ordered by timestamp descending
    scores_cte = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.risk_tier,
            McpLlmAxisScore.created_at,
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=McpLlmAxisScore.created_at.desc(),
            )
            .label("rn"),
        )
        .where(McpLlmAxisScore.created_at >= cutoff)
        .cte("scores")
    )

    latest = (
        select(
            scores_cte.c.server_id,
            scores_cte.c.risk_tier.label("new_tier"),
            scores_cte.c.created_at.label("changed_at"),
        )
        .where(scores_cte.c.rn == 1)
        .cte("latest")
    )

    previous = (
        select(
            scores_cte.c.server_id,
            scores_cte.c.risk_tier.label("old_tier"),
        )
        .where(scores_cte.c.rn == 2)
        .cte("previous")
    )

    stmt = (
        select(
            latest.c.server_id,
            McpServerRegistry.name,
            previous.c.old_tier,
            latest.c.new_tier,
            latest.c.changed_at,
        )
        .join(previous, latest.c.server_id == previous.c.server_id)
        .join(
            McpServerRegistry,
            latest.c.server_id == McpServerRegistry.server_id,
        )
        .where(latest.c.new_tier != previous.c.old_tier)
    )

    rows = session.execute(stmt).all()

    servers = [
        ServerTierChange(
            server_id=row.server_id,
            name=row.name,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            changed_at=row.changed_at,
        )
        for row in rows
    ]

    return TierChangesResponse(servers=servers)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the FastAPI dependency to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    # Insert test data
    with TestSession() as sess:
        # three servers
        servers = [
            McpServerRegistry(server_id=1, name="alpha"),
            McpServerRegistry(server_id=2, name="beta"),
            McpServerRegistry(server_id=3, name="gamma"),
        ]
        sess.add_all(servers)

        now = datetime.datetime.utcnow()
        # each server gets an old tier and a new tier within the last 24h
        scores = [
            # server 1
            McpLlmAxisScore(
                server_id=1,
                risk_tier="low",
                created_at=now - timedelta(hours=23, minutes=30),
            ),
            McpLlmAxisScore(
                server_id=1,
                risk_tier="medium",
                created_at=now - timedelta(hours=1),
            ),
            # server 2
            McpLlmAxisScore(
                server_id=2,
                risk_tier="medium",
                created_at=now - timedelta(hours=22),
            ),
            McpLlmAxisScore(
                server_id=2,
                risk_tier="high",
                created_at=now - timedelta(minutes=45),
            ),
            # server 3
            McpLlmAxisScore(
                server_id=3,
                risk_tier="low",
                created_at=now - timedelta(hours=20),
            ),
            McpLlmAxisScore(
                server_id=3,
                risk_tier="low",  # no change – should be filtered out
                created_at=now - timedelta(minutes=10),
            ),
        ]
        sess.add_all(scores)
        sess.commit()

    # Run the logic using the overridden dependency
    from fastapi import Depends

    # Monkey‑patch the dependency for this isolated run
    original_dep = get_session
    try:
        globals()["get_session"] = get_test_session  # type: ignore
        result = get_risk_tier_transitions()
    finally:
        globals()["get_session"] = original_dep  # restore

    assert isinstance(result, TierChangesResponse)
    assert len(result.servers) == 2  # server 3 had no tier change
    ids = {s.server_id for s in result.servers}
    assert ids == {1, 2}
    print("PASS")