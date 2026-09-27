# services/staged/api_signal_scores_distribution/logic.py

from fastapi import Depends
from pydantic import BaseModel
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

# Real application session provider
from app.db import get_session


class SignalScoreDistribution(BaseModel):
    """Distribution of scores for a single signal type."""
    signal_type: str
    distribution: Dict[str, int]


def _bin_score(score: float) -> str:
    """Return a string representing the 10‑point range a score falls into."""
    start = int(score // 10 * 10)
    end = start + 9
    return f"{start}-{end}"


def get_signal_scores_distribution(
    session: Session = Depends(get_session),
) -> List[SignalScoreDistribution]:
    """
    Read raw scores from the ``mcp_signal_scores`` table and return a
    distribution per signal type.  The distribution maps a score range
    (e.g. ``\"0-9\"``) to the count of scores that fall inside that range.
    """
    # Pull the minimal columns we need directly via raw SQL.
    stmt = text("SELECT signal_type, score FROM mcp_signal_scores")
    rows = session.execute(stmt).fetchall()

    # Aggregate counts.
    agg: Dict[str, Dict[str, int]] = {}
    for signal_type, score in rows:
        bin_key = _bin_score(float(score))
        agg.setdefault(signal_type, {})
        agg[signal_type][bin_key] = agg[signal_type].get(bin_key, 0) + 1

    # Convert to Pydantic models.
    return [
        SignalScoreDistribution(signal_type=stype, distribution=dist)
        for stype, dist in agg.items()
    ]


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine, Column, Float, String, Table, MetaData
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    metadata = MetaData()

    # Define a temporary ``mcp_signal_scores`` table matching the real schema.
    mcp_signal_scores = Table(
        "mcp_signal_scores",
        metadata,
        Column("signal_type", String, nullable=False),
        Column("score", Float, nullable=False),
    )
    metadata.create_all(engine)

    # Seed data: three signal types with varied scores.
    seed = [
        {"signal_type": "typeA", "score": 5},
        {"signal_type": "typeA", "score": 15},
        {"signal_type": "typeA", "score": 25},
        {"signal_type": "typeB", "score": 12},
        {"signal_type": "typeB", "score": 18},
        {"signal_type": "typeC", "score": 30},
        {"signal_type": "typeC", "score": 35},
        {"signal_type": "typeC", "score": 40},
    ]

    with engine.begin() as conn:
        conn.execute(mcp_signal_scores.insert(), seed)

    # Create a session factory that mimics the real ``get_session`` dependency.
    TestSessionLocal = sessionmaker(bind=engine, future=True)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # Run the core logic using the test session.
    test_session = get_test_session()
    result = get_signal_scores_distribution(session=test_session)

    # Basic assertions required by the acceptance criteria.
    assert isinstance(result, list), "Result must be a list"
    assert len(result) == 3, "There should be three signal types"

    # Find the entry for ``typeB`` and verify its known count.
    typeb_entry = next(
        (item for item in result if item.signal_type == "typeB"), None
    )
    assert typeb_entry is not None, "typeB entry missing"
    assert typeb_entry.distribution.get("10-19") == 2, "typeB count mismatch"

    print("PASS")