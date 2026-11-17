from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_content_hash_duplicates


router = APIRouter(prefix="/api")


class DuplicateHashGroup(BaseModel):
    content_hash: str
    server_ids: List[str]
    doc_count: int


class ContentHashDuplicatesResponse(BaseModel):
    duplicate_hashes: List[DuplicateHashGroup]
    total_duplicate_hashes: int
    docs_scanned: int
    distinct_servers: int


@router.get(
    "/ask-corpus/content-hash-duplicates",
    response_model=ContentHashDuplicatesResponse,
)
def content_hash_duplicates(
    session: Session = Depends(get_session),
) -> ContentHashDuplicatesResponse:
    return get_content_hash_duplicates(session)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('server-a', 'first', '[]', 'h1', '2024-01-01 00:00:00'),
                    ('server-b', 'second', '[]', 'h1', '2024-01-02 00:00:00'),
                    ('server-c', 'third', '[]', 'h2', '2024-01-03 00:00:00'),
                    ('server-d', 'fourth', '[]', 'h3', '2024-01-04 00:00:00')
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/ask-corpus/content-hash-duplicates"
    )
    response.raise_for_status()
    payload = response.json()

    assert payload["total_duplicate_hashes"] == 1
    assert payload["docs_scanned"] == 4
    assert payload["distinct_servers"] == 4
    assert len(payload["duplicate_hashes"]) == 1
    assert payload["duplicate_hashes"][0]["content_hash"] == "h1"
    assert payload["duplicate_hashes"][0]["server_ids"] == [
        "server-a",
        "server-b",
    ]
    assert payload["duplicate_hashes"][0]["doc_count"] == 2

    print("PASS")


if __name__ == "__main__":
    _run_self_test()