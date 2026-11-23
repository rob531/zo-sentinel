"""Logic for corpus index freshness calculations over the application database."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


_BUCKETS = ("0-1", "2-7", "8-30", "31-90", "90+")


def _age_days(indexed_at: datetime | None, now: datetime) -> int:
    if indexed_at is None:
        return 90

    value = indexed_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return max(0, int((now - value).total_seconds() // 86400))


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 1:
        return "0-1"
    if age_days <= 7:
        return "2-7"
    if age_days <= 30:
        return "8-30"
    if age_days < 90:
        return "31-90"
    return "90+"


def compute_corpus_freshness(db: Session) -> Dict[str, Any]:
    """Compute document counts and age buckets from the real corpus index."""
    rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
        )
    ).all()

    now = datetime.now(timezone.utc)
    bucket_counts = {bucket: 0 for bucket in _BUCKETS}
    servers = set()
    hashes: Dict[str, list[Any]] = defaultdict(list)

    for server_id, content_hash, indexed_at in rows:
        if server_id is not None:
            servers.add(server_id)
        if content_hash is not None:
            hashes[content_hash].append(server_id)

        bucket_counts[_bucket_for_age(_age_days(indexed_at, now))] += 1

    duplicate_hash_docs = sum(
        len(server_ids)
        for server_ids in hashes.values()
        if len({server_id for server_id in server_ids if server_id is not None}) > 1
    )

    return {
        "total_docs": int(len(rows)),
        "distinct_servers": int(len(servers)),
        "duplicate_hash_docs": int(duplicate_hash_docs),
        "buckets": [
            {"bucket": bucket, "count": int(bucket_counts[bucket])}
            for bucket in _BUCKETS
        ],
    }


def get_corpus_index_freshness(db: Session) -> Dict[str, Any]:
    """Return freshness statistics for the indexed corpus."""
    return compute_corpus_freshness(db)


def corpus_index_freshness(db: Session) -> Dict[str, Any]:
    """Backward-compatible alias for corpus freshness calculations."""
    return compute_corpus_freshness(db)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with TestingSession() as session:
        base_time = datetime.now(timezone.utc)
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="one",
                    terms="alpha",
                    content_hash="hash-a",
                    indexed_at=base_time - timedelta(days=0),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="two",
                    terms="beta",
                    content_hash="hash-b",
                    indexed_at=base_time - timedelta(days=3),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="three",
                    terms="gamma",
                    content_hash="hash-c",
                    indexed_at=base_time - timedelta(days=10),
                ),
                AskCorpusDoc(
                    server_id="server-4",
                    snippet="four",
                    terms="delta",
                    content_hash="hash-shared",
                    indexed_at=base_time - timedelta(days=40),
                ),
                AskCorpusDoc(
                    server_id="server-5",
                    snippet="five",
                    terms="epsilon",
                    content_hash="hash-shared",
                    indexed_at=base_time - timedelta(days=200),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/ask/corpus-freshness")
    def corpus_freshness_endpoint(db: Session = Depends(get_session)):
        return compute_corpus_freshness(db)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus-freshness")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_docs"] == 5
    assert [item["bucket"] for item in payload["buckets"]] == list(_BUCKETS)
    assert payload["duplicate_hash_docs"] == 2
    print("PASS")