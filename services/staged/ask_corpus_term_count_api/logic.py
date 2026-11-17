from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


def _term_count(terms: Any) -> int:
    if terms is None:
        return 0

    if isinstance(terms, str):
        try:
            terms = json.loads(terms)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0

    if not isinstance(terms, list):
        return 0

    return len(terms)


def get_term_count_distribution(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    total = len(rows)
    with_terms = 0
    bucket_counts = {
        "0": 0,
        "1-5": 0,
        "6-20": 0,
        "21+": 0,
    }
    ranked_servers: list[dict[str, Any]] = []

    for server_id, terms in rows:
        count = _term_count(terms)

        if count > 0:
            with_terms += 1

        if count == 0:
            bucket_counts["0"] += 1
        elif count <= 5:
            bucket_counts["1-5"] += 1
        elif count <= 20:
            bucket_counts["6-20"] += 1
        else:
            bucket_counts["21+"] += 1

        ranked_servers.append(
            {
                "server_id": str(server_id) if server_id is not None else "",
                "term_count": count,
            }
        )

    ranked_servers.sort(
        key=lambda item: (-item["term_count"], item["server_id"])
    )

    return {
        "total": total,
        "with_terms": with_terms,
        "empty_terms": total - with_terms,
        "buckets": [
            {"label": label, "count": count}
            for label, count in (
                ("0", bucket_counts["0"]),
                ("1-5", bucket_counts["1-5"]),
                ("6-20", bucket_counts["6-20"]),
                ("21+", bucket_counts["21+"]),
            )
        ],
        "top_servers": ranked_servers[:10],
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)

    with local_session() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-zero",
                    snippet="empty",
                    terms=[],
                    content_hash="hash-zero",
                    indexed_at=datetime(2025, 1, 1),
                ),
                AskCorpusDoc(
                    server_id="server-three",
                    snippet="three",
                    terms=["a", "b", "c"],
                    content_hash="hash-three",
                    indexed_at=datetime(2025, 1, 2),
                ),
                AskCorpusDoc(
                    server_id="server-twelve",
                    snippet="twelve",
                    terms=[str(index) for index in range(12)],
                    content_hash="hash-twelve",
                    indexed_at=datetime(2025, 1, 3),
                ),
                AskCorpusDoc(
                    server_id="server-null",
                    snippet="null",
                    terms=None,
                    content_hash="hash-null",
                    indexed_at=datetime(2025, 1, 4),
                ),
                AskCorpusDoc(
                    server_id="server-empty",
                    snippet="empty",
                    terms=[],
                    content_hash="hash-empty",
                    indexed_at=datetime(2025, 1, 5),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with local_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask-corpus/term-count-distribution")
    def term_count_distribution(payload: dict = Depends(get_term_count_distribution)):
        return payload

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
    assert len(payload["top_servers"]) == 5
    assert payload["top_servers"][0]["term_count"] == 12

    print("PASS")