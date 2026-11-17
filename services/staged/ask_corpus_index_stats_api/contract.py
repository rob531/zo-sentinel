import json
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session
from app.models import AskCorpusDoc

router = APIRouter(prefix="/api")


class TermCount(BaseModel):
    term: str
    count: int


class CorpusStats(BaseModel):
    total_docs: int
    distinct_servers: int
    distinct_content_hashes: int
    duplicate_hash_groups: int
    oldest_indexed_at: datetime
    newest_indexed_at: datetime
    stale_docs_older_than_days: int
    top_terms: List[TermCount]


@router.get("/ask/corpus-index-stats", response_model=CorpusStats)
def get_corpus_index_stats(
    days: int = Query(30, ge=0),
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
) -> CorpusStats:
    now = datetime.utcnow()

    total_docs = session.scalar(select(func.count()).select_from(AskCorpusDoc))
    distinct_servers = session.scalar(
        select(func.count(func.distinct(AskCorpusDoc.server_id)))
    )
    distinct_content_hashes = session.scalar(
        select(func.count(func.distinct(AskCorpusDoc.content_hash)))
    )
    duplicate_subq = (
        select(AskCorpusDoc.content_hash)
        .group_by(AskCorpusDoc.content_hash)
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_hash_groups = session.scalar(select(func.count()).select_from(duplicate_subq))
    oldest_indexed_at = session.scalar(select(func.min(AskCorpusDoc.indexed_at)))
    newest_indexed_at = session.scalar(select(func.max(AskCorpusDoc.indexed_at)))
    stale_docs_older_than_days = session.scalar(
        select(func.count()).where(
            AskCorpusDoc.indexed_at < now - timedelta(days=days)
        )
    )

    term_rows = session.execute(
        text(
            """
            SELECT term, COUNT(*) AS count FROM (
                SELECT json_each.value AS term
                FROM ask_corpus_index, json_each(terms)
            ) sub
            GROUP BY term
            ORDER BY count DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()
    top_terms = [TermCount(term=row[0], count=row[1]) for row in term_rows]

    return CorpusStats(
        total_docs=total_docs or 0,
        distinct_servers=distinct_servers or 0,
        distinct_content_hashes=distinct_content_hashes or 0,
        duplicate_hash_groups=duplicate_hash_groups or 0,
        oldest_indexed_at=oldest_indexed_at,
        newest_indexed_at=newest_indexed_at,
        stale_docs_older_than_days=stale_docs_older_than_days or 0,
        top_terms=top_terms,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    # In‑memory SQLite for self‑test
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    AskCorpusDoc.metadata.create_all(engine)

    # Seed data
    now = datetime.utcnow()
    with SessionLocal() as s:
        s.add_all(
            [
                AskCorpusDoc(
                    server_id=1,
                    snippet="doc1",
                    terms=["alpha", "beta"],
                    content_hash="hashdup",
                    indexed_at=now - timedelta(days=10),
                ),
                AskCorpusDoc(
                    server_id=2,
                    snippet="doc2",
                    terms=["alpha", "gamma"],
                    content_hash="hashdup",
                    indexed_at=now - timedelta(days=5),
                ),
                AskCorpusDoc(
                    server_id=3,
                    snippet="doc3",
                    terms=["delta"],
                    content_hash="hashuniq",
                    indexed_at=datetime(2020, 1, 1),
                ),
            ]
        )
        s.commit()

    # Override dependency
    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # Run test client
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/ask/corpus-index-stats?days=30&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_docs"] == 3
    assert data["duplicate_hash_groups"] == 1
    assert data["stale_docs_older_than_days"] == 1
    print("PASS")