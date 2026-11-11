from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc
from .logic import get_term_count_distribution

router = APIRouter(prefix="/api")


class TermCountBucket(BaseModel):
    label: str
    count: int


class TopServerTermCount(BaseModel):
    server_id: str
    term_count: int


class TermCountDistributionResponse(BaseModel):
    total: int
    with_terms: int
    empty_terms: int
    buckets: List[TermCountBucket]
    top_servers: List[TopServerTermCount]


@router.get(
    "/ask-corpus/term-count-distribution",
    response_model=TermCountDistributionResponse,
)
def term_count_distribution(
    session: Session = Depends(get_session),
) -> TermCountDistributionResponse:
    return get_term_count_distribution(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id VARCHAR,
                    snippet TEXT,
                    terms JSON,
                    content_hash VARCHAR,
                    indexed_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('server-a', '', '[]', 'hash-a', :now),
                    ('server-b', '', '["one", "two", "three"]', 'hash-b', :now),
                    ('server-c', '', :terms_c, 'hash-c', :now),
                    ('server-d', '', NULL, 'hash-d', :now),
                    ('server-e', '', '[]', 'hash-e', :now)
                """
            ),
            {
                "now": datetime.now(timezone.utc),
                "terms_c": '["one", "two", "three", "four", "five", "six", '
                '"seven", "eight", "nine", "ten", "eleven", "twelve"]',
            },
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    response = TestClient(that_app).get(
        "/api/ask-corpus/term-count-distribution"
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 5
    assert payload["with_terms"] == 2
    assert payload["empty_terms"] == 3
    assert payload["buckets"][1]["label"] == "1-5"
    assert payload["buckets"][1]["count"] == 1
    assert payload["buckets"][3]["label"] == "21+"
    assert payload["buckets"][3]["count"] == 0
    assert AskCorpusDoc.__tablename__ == "ask_corpus_index"

    print("PASS")