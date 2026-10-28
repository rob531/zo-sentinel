from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

try:
    from .logic import get_corpus_terms_facets as _logic_terms_facets
except ImportError:
    try:
        from .logic import get_terms_facets as _logic_terms_facets
    except ImportError:
        try:
            from .logic import aggregate_corpus_terms as _logic_terms_facets
        except ImportError:
            _logic_terms_facets = None


class TermFacet(BaseModel):
    term: str
    doc_count: int


class CorpusTermsFacetsResponse(BaseModel):
    total_docs: int = Field(ge=0)
    scanned: int = Field(ge=0)
    top_terms: list[TermFacet] = Field(default_factory=list)


router = APIRouter(prefix="/api", tags=["ask-corpus"])


def _extract_terms(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]

    if isinstance(value, dict):
        candidates: list[Any] = list(value.keys())
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]

    terms: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            term = candidate.strip()
        elif isinstance(candidate, dict):
            raw_term = candidate.get("term") or candidate.get("name") or candidate.get("value")
            term = str(raw_term).strip() if raw_term is not None else ""
        else:
            term = str(candidate).strip()
        if term:
            terms.append(term)
    return terms


def _build_corpus_terms_facets(session: Session) -> dict[str, Any]:
    total_docs = int(session.query(AskCorpusDoc).count())
    documents = (
        session.query(AskCorpusDoc)
        .order_by(AskCorpusDoc.indexed_at.desc())
        .limit(5000)
        .all()
    )

    counts: dict[str, int] = {}
    for document in documents:
        for term in set(_extract_terms(document.terms)):
            counts[term] = counts.get(term, 0) + 1

    top_terms = [
        {"term": term, "doc_count": count}
        for term, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "total_docs": total_docs,
        "scanned": len(documents),
        "top_terms": top_terms,
    }


@router.get(
    "/ask/corpus/terms",
    response_model=CorpusTermsFacetsResponse,
    summary="Aggregate query-term facets over the indexed ask corpus",
)
async def get_corpus_terms_facets(
    session: Session = Depends(get_session),
) -> Any:
    implementation = _logic_terms_facets or _build_corpus_terms_facets
    result = implementation(session)
    if inspect.isawaitable(result):
        result = await result
    return result


def _run_self_test() -> None:
    from fastapi import FastAPI

    class _Query:
        def __init__(self, rows: list[AskCorpusDoc]) -> None:
            self.rows = rows
            self._limit: int | None = None

        def order_by(self, *args: Any) -> "_Query":
            return self

        def limit(self, value: int) -> "_Query":
            self._limit = value
            return self

        def count(self) -> int:
            return len(self.rows)

        def all(self) -> list[AskCorpusDoc]:
            rows = sorted(
                self.rows,
                key=lambda row: row.indexed_at,
                reverse=True,
            )
            return rows[: self._limit] if self._limit is not None else rows

    class _Session:
        def __init__(self, rows: list[AskCorpusDoc]) -> None:
            self.rows = rows

        def query(self, model: Any) -> _Query:
            return _Query(self.rows)

    now = datetime.now(timezone.utc)
    rows = [
        AskCorpusDoc(
            server_id="server-a",
            terms=["python", "security", "mcp"],
            content_hash="hash-a",
            indexed_at=now,
            snippet="first document",
        ),
        AskCorpusDoc(
            server_id="server-b",
            terms=["python", "database"],
            content_hash="hash-b",
            indexed_at=now,
            snippet="second document",
        ),
        AskCorpusDoc(
            server_id="server-c",
            terms=["security", "database"],
            content_hash="hash-c",
            indexed_at=now,
            snippet="third document",
        ),
    ]
    fake_session = _Session(rows)

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: fake_session

    global _logic_terms_facets
    original_logic = _logic_terms_facets
    _logic_terms_facets = _build_corpus_terms_facets

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    try:
        import asyncio

        asyncio.run(
            test_app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/api/ask/corpus/terms",
                    "raw_path": b"/api/ask/corpus/terms",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [],
                    "client": ("127.0.0.1", 1),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
        )
    finally:
        _logic_terms_facets = original_logic

    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    payload = json.loads(body)

    assert start["status"] == 200
    assert payload["total_docs"] == 3
    assert payload["scanned"] == 3
    assert payload["top_terms"]
    assert next(item for item in payload["top_terms"] if item["term"] == "security")["doc_count"] == 2
    print("PASS")


if __name__ == "__main__":
    _run_self_test()