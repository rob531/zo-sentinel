from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def get_duplicate_hashes(session: Session = Depends(get_session)) -> dict[str, Any]:
    total_docs = session.execute(
        select(func.count()).select_from(AskCorpusDoc)
    ).scalar_one()

    grouped = session.execute(
        select(
            AskCorpusDoc.content_hash,
            func.count().label("doc_count"),
            func.min(AskCorpusDoc.indexed_at).label("first_indexed_at"),
            func.max(AskCorpusDoc.indexed_at).label("last_indexed_at"),
        )
        .where(AskCorpusDoc.content_hash.is_not(None))
        .group_by(AskCorpusDoc.content_hash)
        .having(func.count() > 1)
        .order_by(AskCorpusDoc.content_hash)
    ).all()

    duplicate_groups: list[dict[str, Any]] = []
    for group in grouped:
        documents = session.execute(
            select(AskCorpusDoc)
            .where(AskCorpusDoc.content_hash == group.content_hash)
            .order_by(AskCorpusDoc.indexed_at, AskCorpusDoc.server_id)
        ).scalars().all()

        duplicate_groups.append(
            {
                "content_hash": group.content_hash,
                "doc_count": int(group.doc_count),
                "server_ids": [document.server_id for document in documents],
                "snippets": [document.snippet for document in documents],
                "first_indexed_at": group.first_indexed_at,
                "last_indexed_at": group.last_indexed_at,
            }
        )

    duplicate_doc_count = sum(group["doc_count"] for group in duplicate_groups)

    return {
        "total_docs": int(total_docs),
        "duplicate_groups": duplicate_groups,
        "duplicate_doc_count": duplicate_doc_count,
    }


def get_duplicate_hash_groups(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_duplicate_hashes(session)


def compute_duplicate_hashes(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_duplicate_hashes(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            AskCorpusDoc(
                server_id="server-1",
                snippet="first document",
                terms=None,
                content_hash="h1",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-2",
                snippet="second document",
                terms=None,
                content_hash="h1",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-3",
                snippet="unique document",
                terms=None,
                content_hash="h2",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="server-4",
                snippet="another unique document",
                terms=None,
                content_hash="h3",
                indexed_at=now,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/ask/corpus/duplicate-hashes")
    def duplicate_hashes_endpoint(session: Session = Depends(get_session)):
        return get_duplicate_hashes(session)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus/duplicate-hashes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_docs"] == 4
    assert len(payload["duplicate_groups"]) == 1
    assert payload["duplicate_groups"][0]["doc_count"] == 2
    assert payload["duplicate_groups"][0]["content_hash"] == "h1"
    print("PASS")