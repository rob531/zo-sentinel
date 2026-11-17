from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

router = APIRouter(prefix="/api", tags=["ask"])


def _build_duplicate_report(
    rows: List[Any],
) -> Dict[str, Any]:
    hash_groups: Dict[str, List[str]] = defaultdict(list)
    servers_without_hash = 0

    for row in rows:
        content_hash = row.content_hash
        server_id = row.server_id

        if content_hash is None or content_hash == "":
            servers_without_hash += 1
            continue

        hash_groups[str(content_hash)].append(
            "" if server_id is None else str(server_id)
        )

    duplicate_groups = [
        {
            "content_hash": content_hash,
            "doc_count": len(server_ids),
            "server_ids": server_ids,
        }
        for content_hash, server_ids in hash_groups.items()
        if len(server_ids) > 1
    ]
    duplicate_groups.sort(
        key=lambda group: (-group["doc_count"], group["content_hash"])
    )

    duplicate_doc_count = sum(
        group["doc_count"] for group in duplicate_groups
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_docs": len(rows),
        "duplicate_groups": duplicate_groups[:200],
        "duplicate_doc_count": duplicate_doc_count,
        "servers_without_hash": servers_without_hash,
    }


def get_duplicate_corpus_docs(session: Session) -> Dict[str, Any]:
    statement = select(
        AskCorpusDoc.server_id,
        AskCorpusDoc.snippet,
        AskCorpusDoc.terms,
        AskCorpusDoc.content_hash,
        AskCorpusDoc.indexed_at,
    )
    rows = list(session.execute(statement).all())
    return _build_duplicate_report(rows)


@router.get("/ask/corpus-duplicates")
def get_corpus_duplicates(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_duplicate_corpus_docs(session)


get_corpus_duplicate_docs = get_corpus_duplicates
get_duplicate_docs = get_corpus_duplicates


if __name__ == "__main__":
    from datetime import datetime as datetime_type

    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as test_session:
        test_session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="First duplicate document",
                    terms="alpha,beta",
                    content_hash="shared-hash",
                    indexed_at=datetime_type(2024, 1, 1, 0, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="Second duplicate document",
                    terms="alpha,gamma",
                    content_hash="shared-hash",
                    indexed_at=datetime_type(2024, 1, 2, 0, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="Document without a hash",
                    terms="delta",
                    content_hash="",
                    indexed_at=datetime_type(2024, 1, 3, 0, 0, 0),
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/ask/corpus-duplicates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["duplicate_doc_count"] == 2
    assert len(payload["duplicate_groups"]) == 1
    assert payload["duplicate_groups"][0]["doc_count"] == 2
    assert payload["servers_without_hash"] == 1

    print("PASS")