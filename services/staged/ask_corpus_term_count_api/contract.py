from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


class TermCountBucket(BaseModel):
    label: str
    count: int


class TermCountServer(BaseModel):
    server_id: str
    term_count: int


class TermCountDistributionResponse(BaseModel):
    total: int
    with_terms: int
    empty_terms: int
    buckets: List[TermCountBucket]
    top_servers: List[TermCountServer]


def _terms_length(terms: Any) -> int:
    if terms is None:
        return 0
    if isinstance(terms, list):
        return len(terms)
    if isinstance(terms, str):
        try:
            decoded = json.loads(terms)
        except (TypeError, ValueError):
            return 0
        if isinstance(decoded, list):
            return len(decoded)
    return 0


def get_term_count_distribution(session: Session) -> Dict[str, Any]:
    rows = session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    term_counts = [
        (row.server_id, _terms_length(row.terms))
        for row in rows
    ]

    total = len(term_counts)
    with_terms = sum(1 for _, count in term_counts if count > 0)
    empty_terms = total - with_terms

    buckets = [
        {"label": "0", "count": 0},
        {"label": "1-5", "count": 0},
        {"label": "6-20", "count": 0},
        {"label": "21+", "count": 0},
    ]

    for _, count in term_counts:
        if count == 0:
            buckets[0]["count"] += 1
        elif count <= 5:
            buckets[1]["count"] += 1
        elif count <= 20:
            buckets[2]["count"] += 1
        else:
            buckets[3]["count"] += 1

    top_servers = [
        {"server_id": server_id, "term_count": count}
        for server_id, count in sorted(
            term_counts,
            key=lambda item: (-item[1], item[0]),
        )[:10]
    ]

    return {
        "total": total,
        "with_terms": with_terms,
        "empty_terms": empty_terms,
        "buckets": buckets,
        "top_servers": top_servers,
    }


router = APIRouter(prefix="/api")


@router.get(
    "/ask-corpus/term-count-distribution",
    response_model=TermCountDistributionResponse,
)
def term_count_distribution(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_term_count_distribution(session)


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    now = datetime.now(timezone.utc)
    seeded_rows = [
        AskCorpusDoc(
            server_id="server-empty",
            snippet="empty array",
            terms=[],
            content_hash="hash-empty",
            indexed_at=now,
        ),
        AskCorpusDoc(
            server_id="server-three",
            snippet="three terms",
            terms=["alpha", "beta", "gamma"],
            content_hash="hash-three",
            indexed_at=now,
        ),
        AskCorpusDoc(
            server_id="server-twelve",
            snippet="twelve terms",
            terms=[f"term-{index}" for index in range(12)],
            content_hash="hash-twelve",
            indexed_at=now,
        ),
        AskCorpusDoc(
            server_id="server-null",
            snippet="null terms",
            terms=None,
            content_hash="hash-null",
            indexed_at=now,
        ),
        AskCorpusDoc(
            server_id="server-empty-two",
            snippet="another empty array",
            terms=[],
            content_hash="hash-empty-two",
            indexed_at=now,
        ),
    ]
    testing_session.add_all(seeded_rows)
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/ask-corpus/term-count-distribution"
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 5
            assert payload["with_terms"] == 2
            assert payload["empty_terms"] == 3

            buckets = {
                bucket["label"]: bucket["count"]
                for bucket in payload["buckets"]
            }
            assert buckets["1-5"] == 1
            assert buckets["6-20"] == 1
            assert buckets["21+"] == 0
            assert buckets["0"] == 3
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()