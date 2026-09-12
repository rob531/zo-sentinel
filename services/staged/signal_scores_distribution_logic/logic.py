"""
Logic component for signal scores distribution.
Computes 10 equal-width bins from 0-100 for signal scores.
"""
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session


def get_signal_scores_distribution(
    signal_name: str,
    session: Session | None = None
) -> dict[str, Any]:
    """
    Compute distribution of signal scores for a given signal_name.
    
    Reads mcp_signal_scores from the ZoComputer store, computes 10 equal-width
    bins from 0-100, and returns the distribution.
    
    Args:
        signal_name: The signal name to compute distribution for.
        session: Optional database session. If not provided, reads from
                 the ZoComputer store (127.0.0.1:8772).
    
    Returns:
        dict with structure: {signal, bins: [{bin, count}]}
    """
    if session is not None:
        return _compute_from_session(signal_name, session)
    return _compute_from_store(signal_name)


def _compute_from_session(signal_name: str, session: Session) -> dict[str, Any]:
    """Compute bins from an existing session (for testing)."""
    result = session.execute(
        text("SELECT score FROM mcp_signal_scores WHERE signal_name = :name"),
        {"name": signal_name}
    )
    scores = [row[0] for row in result.fetchall()]
    return _build_distribution(signal_name, scores)


def _compute_from_store(signal_name: str) -> dict[str, Any]:
    """Read from ZoComputer store and compute bins."""
    import urllib.request
    import json
    
    query = {
        "sql": "SELECT score FROM mcp_signal_scores WHERE signal_name = :name",
        "params": {"name": signal_name}
    }
    
    req = urllib.request.Request(
        "http://127.0.0.1:8772/query",
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    
    scores = [row["score"] for row in data.get("rows", [])]
    return _build_distribution(signal_name, scores)


def _build_distribution(signal_name: str, scores: list[float | int]) -> dict[str, Any]:
    """Build the bin distribution from scores."""
    num_bins = 10
    bin_width = 100.0 / num_bins
    
    # Initialize bins: 0-10, 10-20, ..., 90-100
    bins = [
        {"bin": f"{int(i * bin_width)}-{int((i + 1) * bin_width)}", "count": 0}
        for i in range(num_bins)
    ]
    
    # Count scores into bins
    for score in scores:
        if score is None:
            continue
        # Handle edge case where score is exactly 100
        if score >= 100:
            bin_idx = num_bins - 1
        else:
            bin_idx = int(score / bin_width)
        bin_idx = max(0, min(bin_idx, num_bins - 1))
        bins[bin_idx]["count"] += 1
    
    return {"signal": signal_name, "bins": bins}


if __name__ == "__main__":
    # Self-test with in-memory SQLite store
    from fastapi import FastAPI
    from app.db import get_session
    
    # Create in-memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create table and insert test data
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_signal_scores (
                id INTEGER PRIMARY KEY,
                signal_name TEXT,
                score REAL
            )
        """))
        conn.commit()
        
        # Insert test data with known distribution
        # Scores: 5 values in 0-10, 3 in 20-30, 2 in 90-100
        test_scores = [
            (1, "test_signal", 5.0),
            (2, "test_signal", 7.5),
            (3, "test_signal", 3.2),
            (4, "test_signal", 9.9),
            (5, "test_signal", 1.0),
            (6, "test_signal", 25.0),
            (7, "test_signal", 28.5),
            (8, "test_signal", 22.1),
            (9, "test_signal", 95.0),
            (10, "test_signal", 98.5),
        ]
        
        for row in test_scores:
            conn.execute(
                text("INSERT INTO mcp_signal_scores (id, signal_name, score) VALUES (:id, :name, :score)"),
                {"id": row[0], "name": row[1], "score": row[2]}
            )
        conn.commit()
    
    # Create session factory
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Create FastAPI app and override dependency
    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    
    # Run test with overridden session
    with engine.connect() as conn:
        test_session = TestingSessionLocal()
        result = get_signal_scores_distribution("test_signal", session=test_session)
        test_session.close()
    
    # Verify results
    expected = {
        "signal": "test_signal",
        "bins": [
            {"bin": "0-10", "count": 5},
            {"bin": "10-20", "count": 0},
            {"bin": "20-30", "count": 3},
            {"bin": "30-40", "count": 0},
            {"bin": "40-50", "count": 0},
            {"bin": "50-60", "count": 0},
            {"bin": "60-70", "count": 0},
            {"bin": "70-80", "count": 0},
            {"bin": "80-90", "count": 0},
            {"bin": "90-100", "count": 2},
        ]
    }
    
    assert result == expected, f"Expected {expected}, got {result}"
    print("PASS")