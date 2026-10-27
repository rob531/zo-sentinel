from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterator, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


class TopServer(BaseModel):
    server_id: str
    count: int


class TermOverlap(BaseModel):
    term: str
    doc_count: int
    server_count: int
    top_servers: List[TopServer]


class OverlapPair(BaseModel):
    term_a: str
    term_b: str
    shared_servers: List[str]


class TermOverlapResponse(BaseModel):
    total_docs: int
    terms: List[TermOverlap]
    overlap_pairs: List[OverlapPair]


def _parse_terms(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, (list, tuple, set)):
        return []

    terms = []
    for item in value:
        if not isinstance(item, str):
            continue
        term = item.strip()
        if term:
            terms.append(term)
    return terms


def _term_overlap(session: Session, top: int) -> TermOverlapResponse:
    rows = session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    term_docs = {}
    term_servers = {}

    for row in rows:
        server_id = str(row.server_id)
        for term in set(_parse_terms(row.terms)):
            term_docs.setdefault(term, set()).add(id(row))
            term_servers.setdefault(term, set()).add(server_id)

    ordered_terms = sorted(
        term_docs,
        key=lambda term: (-len(term_docs[term]), term),
    )[:top]

    term_results = []
    for term in ordered_terms:
        servers = term_servers[term]
        server_counts = {
            server_id: sum(
                1
                for row in rows
                if str(row.server_id) == server_id
                and term in _parse_terms(row.terms)
            )
            for server_id in servers
        }
        top_servers = [
            TopServer(server_id=server_id, count=count)
            for server_id, count in sorted(
                server_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        term_results.append(
            TermOverlap(
                term=term,
                doc_count=len(term_docs[term]),
                server_count=len(servers),
                top_servers=top_servers,
            )
        )

    overlap_pairs = []
    for index, term_a in enumerate(ordered_terms):
        for term_b in ordered_terms[index + 1 :]:
            shared_servers = sorted(
                term_servers[term_a].intersection(term_servers[term_b])
            )
            if shared_servers:
                overlap_pairs.append(
                    OverlapPair(
                        term_a=term_a,
                        term_b=term_b,
                        shared_servers=shared_servers,
                    )
                )

    return TermOverlapResponse(
        total_docs=len(rows),
        terms=term_results,
        overlap_pairs=overlap_pairs,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/ask-corpus/term-overlap",
    response_model=TermOverlapResponse,
)
def ask_corpus_term_overlap(
    top: int = Query(default=20, ge=1),
    session: Session = Depends(get_session),
) -> TermOverlapResponse:
    return _term_overlap(session, top)


app = FastAPI()
app.include_router(router)


def run_self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    indexed_at = datetime.utcnow()
    with session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="Python security guidance",
                    terms=["python", "security"],
                    content_hash="hash-a",
                    indexed_at=indexed_at,
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="Python database notes",
                    terms=["python", "database"],
                    content_hash="hash-b",
                    indexed_at=indexed_at,
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="Python security network notes",
                    terms=["python", "security", "network"],
                    content_hash="hash-c",
                    indexed_at=indexed_at,
                ),
            ]
        )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/ask-corpus/term-overlap?top=20")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_docs"] == 3
        assert payload["terms"]

        terms = {item["term"]: item for item in payload["terms"]}
        assert "python" in terms
        assert "security" in terms
        assert all(item["doc_count"] >= 1 for item in payload["terms"])

        assert terms["python"]["server_count"] == 3
        assert terms["security"]["server_count"] == 2

        overlap = next(
            pair
            for pair in payload["overlap_pairs"]
            if {pair["term_a"], pair["term_b"]} == {"python", "security"}
        )
        assert set(overlap["shared_servers"]) == {"server-a", "server-c"}

    print("PASS")


if __name__ == "__main__":
    run_self_test()