# services/staged/ask_corpus_health_api/logic.py

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import AskCorpusDoc, McpServerRegistry


def _median_timestamp(timestamps: List[datetime]) -> Optional[datetime]:
    """Return the median datetime from a list, or None if empty."""
    n = len(timestamps)
    if n == 0:
        return None
    timestamps.sort()
    mid = n // 2
    if n % 2 == 1:
        return timestamps[mid]
    # even count – average the two middle values
    a = timestamps[mid - 1]
    b = timestamps[mid]
    return datetime.fromtimestamp((a.timestamp() + b.timestamp()) / 2)


def get_corpus_health(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Compute corpus health statistics.

    Returns a dict with:
        corpus_coverage (float): indexed_servers / total_servers
        total_servers (int)
        indexed_servers (int)
        median_indexed_at (str | None): ISO‑8601 timestamp
        oldest_indexed_at (str | None): ISO‑8601 timestamp
        index_size (int): total rows in ask_corpus_index
    """
    # total servers
    total_servers = db.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar_one()

    # indexed servers (distinct server_id in AskCorpusDoc)
    indexed_servers = db.execute(
        select(func.count(func.distinct(AskCorpusDoc.server_id)))
    ).scalar_one()

    corpus_coverage = (
        indexed_servers / total_servers if total_servers else 0.0
    )

    # total indexed documents
    index_size = db.execute(
        select(func.count()).select_from(AskCorpusDoc)
    ).scalar_one()

    # collect indexed_at timestamps
    timestamps = db.execute(
        select(AskCorpusDoc.indexed_at).where(AskCorpusDoc.indexed_at.is_not(None))
    ).scalars().all()

    # oldest
    oldest_dt: Optional[datetime] = min(timestamps) if timestamps else None

    # median – use DB function when possible, otherwise Python fallback
    median_dt: Optional[datetime] = None
    if timestamps:
        if db.bind.dialect.name == "postgresql":
            median_dt = db.execute(
                select(
                    func.percentile_cont(0.5).within_group(AskCorpusDoc.indexed_at)
                )
            ).scalar_one_or_none()
        else:
            median_dt = _median_timestamp(timestamps)

    def _to_iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None

    return {
        "corpus_coverage": corpus_coverage,
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "median_indexed_at": _to_iso(median_dt),
        "oldest_indexed_at": _to_iso(oldest_dt),
        "index_size": index_size,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Helper to provide a session dependency
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    with SessionLocal() as db:
        # 5 servers
        servers = [
            McpServerRegistry(server_id="s1", name="Server 1"),
            McpServerRegistry(server_id="s2", name="Server 2"),
            McpServerRegistry(server_id="s3", name="Server 3"),
            McpServerRegistry(server_id="s4", name="Server 4"),
            McpServerRegistry(server_id="s5", name="Server 5"),
        ]
        db.add_all(servers)

        # 3 indexed documents (servers s1, s2, s3)
        docs = [
            AskCorpusDoc(
                content_hash="h1",
                indexed_at=datetime(2023, 1, 10, 12, 0, 0),
                server_id="s1",
                snippet="doc1",
                terms="term1",
            ),
            AskCorpusDoc(
                content_hash="h2",
                indexed_at=datetime(2023, 1, 12, 15, 30, 0),
                server_id="s2",
                snippet="doc2",
                terms="term2",
            ),
            AskCorpusDoc(
                content_hash="h3",
                indexed_at=datetime(2023, 1, 14, 9, 45, 0),
                server_id="s3",
                snippet="doc3",
                terms="term3",
            ),
        ]
        db.add_all(docs)
        db.commit()

        # Run the logic
        result = get_corpus_health(db)

    # Assertions per acceptance criteria
    assert result["total_servers"] == 5, "total_servers should be 5"
    assert result["indexed_servers"] == 3, "indexed_servers should be 3"
    expected_coverage = 3 / 5
    assert (
        abs(result["corpus_coverage"] - expected_coverage) < 1e-6
    ), f"corpus_coverage should be {expected_coverage}"
    # median of [2023-01-10, 2023-01-12, 2023-01-14] is 2023-01-12
    assert result["median_indexed_at"] == "2023-01-12T15:30:00", "median_indexed_at mismatch"
    # oldest is 2023-01-10
    assert result["oldest_indexed_at"] == "2023-01-10T12:00:00", "oldest_indexed_at mismatch"
    assert result["index_size"] == 3, "index_size should be 3"

    print("PASS")