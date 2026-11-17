from collections import Counter
import json
from typing import Any, Dict, Iterable, List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


class TermCount(BaseModel):
    term: str
    count: int


class TermUbiquityResponse(BaseModel):
    terms: List[TermCount]


def _extract_terms(value: Any) -> Iterable[str]:
    if value is None:
        return []

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [part.strip() for part in value.split(",") if part.strip()]

    if isinstance(value, dict):
        if "terms" in value:
            value = value["terms"]
        else:
            value = list(value.keys())

    if not isinstance(value, (list, tuple, set)):
        value = [value]

    terms = []
    for term in value:
        if term is None:
            continue
        if not isinstance(term, str):
            term = str(term)
        term = term.strip()
        if term:
            terms.append(term)
    return terms


def get_term_ubiquity(
    session: Session = Depends(get_session),
) -> TermUbiquityResponse:
    rows = session.execute(select(AskCorpusDoc.terms)).scalars().all()

    counts: Counter[str] = Counter()
    for value in rows:
        counts.update(set(_extract_terms(value)))

    ordered_terms = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return TermUbiquityResponse(
        terms=[TermCount(term=term, count=count) for term, count in ordered_terms]
    )


term_ubiquity = get_term_ubiquity
ask_corpus_term_ubiquity = get_term_ubiquity


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    content_hash VARCHAR PRIMARY KEY,
                    indexed_at DATETIME,
                    server_id VARCHAR,
                    snippet TEXT,
                    terms JSON
                )
                """
            )
        )
        rows = [
            (
                "hash-1",
                datetime.now(timezone.utc).isoformat(),
                "server-1",
                "alpha and beta",
                json.dumps(["alpha", "beta"]),
            ),
            (
                "hash-2",
                datetime.now(timezone.utc).isoformat(),
                "server-2",
                "beta and gamma",
                json.dumps(["beta", "gamma"]),
            ),
            (
                "hash-3",
                datetime.now(timezone.utc).isoformat(),
                "server-3",
                "gamma and alpha",
                json.dumps(["gamma", "alpha"]),
            ),
        ]
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index (
                    content_hash, indexed_at, server_id, snippet, terms
                ) VALUES (
                    :content_hash, :indexed_at, :server_id, :snippet, :terms
                )
                """
            ),
            [
                {
                    "content_hash": row[0],
                    "indexed_at": row[1],
                    "server_id": row[2],
                    "snippet": row[3],
                    "terms": row[4],
                }
                for row in rows
            ],
        )

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get(
        "/api/ask/corpus/term-ubiquity",
        response_model=TermUbiquityResponse,
    )
    def term_ubiquity_endpoint(
        session: Session = Depends(get_session),
    ) -> TermUbiquityResponse:
        return get_term_ubiquity(session=session)

    with TestClient(test_app) as client:
        response = client.get("/api/ask/corpus/term-ubiquity")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["terms"]) == 3
        counts = {item["term"]: item["count"] for item in payload["terms"]}
        assert counts["alpha"] == 2
        assert counts["beta"] == 2
        assert counts["gamma"] == 2

    print("PASS")