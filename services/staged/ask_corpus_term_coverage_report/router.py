"""ask_corpus_term_coverage_report.py -- corpus term-coverage report (read-only).

Summarises how well the ask_corpus_index table covers the registry's server
set and how rich the stored term vectors are. Pure report logic (stdlib only)
plus a thin read-only FastAPI route over the REAL app data layer
(app.db / app.models) -- no inline stubs, no duckdb.

Route: GET /api/ask/corpus/term-coverage-report
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

router = APIRouter(prefix="/api", tags=["ask"])

TOP_TERMS_LIMIT = 25


# ---------------------------------------------------------------- pure logic
def _flatten_terms(value: Any) -> List[str]:
    """Normalise a stored terms value to a flat list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        out: List[str] = []
        for item in value.values():
            out.extend(_flatten_terms(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_flatten_terms(item))
        return out
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _doc_terms(row: Dict[str, Any]) -> List[str]:
    return _flatten_terms(row.get("terms"))


def build_report(registry_rows: List[Dict[str, Any]],
                 corpus_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Coverage + term-richness report. Pure: no DB, no network."""
    registry_ids = set()
    for row in registry_rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("server_id")
        if sid:
            registry_ids.add(str(sid))

    docs_by_server: Dict[str, List[Dict[str, Any]]] = {}
    for row in corpus_rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("server_id")
        if sid is None:
            continue
        docs_by_server.setdefault(str(sid), []).append(row)

    covered = sorted(sid for sid in docs_by_server if sid in registry_ids)

    terms_per_doc: List[int] = []
    empty_term_docs = 0
    term_counts: Counter = Counter()
    for sid in covered:
        for row in docs_by_server[sid]:
            terms = sorted(set(_doc_terms(row)))
            terms_per_doc.append(len(terms))
            if not terms:
                empty_term_docs += 1
            term_counts.update(terms)

    servers_total = len(registry_ids)
    servers_with_docs = len(covered)
    coverage_pct = (round(servers_with_docs / servers_total * 100.0, 1)
                    if servers_total else 0.0)

    top_terms = [
        {"term": term, "count": count}
        for term, count in sorted(term_counts.items(),
                                  key=lambda kv: (-kv[1], kv[0]))[:TOP_TERMS_LIMIT]
    ]

    return {
        "servers_total": servers_total,
        "servers_with_docs": servers_with_docs,
        "coverage_pct": coverage_pct,
        "mean_terms_per_doc": (float(statistics.mean(terms_per_doc))
                               if terms_per_doc else 0.0),
        "median_terms_per_doc": (float(statistics.median(terms_per_doc))
                                 if terms_per_doc else 0.0),
        "empty_term_docs": empty_term_docs,
        "top_terms": top_terms,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Human-readable markdown summary of a report dict."""
    report = report or {}
    lines = [
        "# Ask Corpus Term Coverage",
        "",
        f"- Registry servers: {report.get('servers_total', 0)}",
        f"- Servers with corpus docs: {report.get('servers_with_docs', 0)}",
        f"- Coverage: {report.get('coverage_pct', 0.0)}%",
        f"- Mean terms per doc: {report.get('mean_terms_per_doc', 0.0)}",
        f"- Median terms per doc: {report.get('median_terms_per_doc', 0.0)}",
        f"- Docs with empty terms: {report.get('empty_term_docs', 0)}",
        "",
        "## Top terms",
        "",
    ]
    top_terms = report.get("top_terms") or []
    if not top_terms:
        lines.append("_none_")
    else:
        lines.append("| term | count |")
        lines.append("| --- | --- |")
        for item in top_terms:
            lines.append(f"| {item.get('term', '')} | {item.get('count', 0)} |")
    return "\n".join(lines)


# ------------------------------------------------------------- data layer
def load_rows(db: Session):
    """Read the registry server set + corpus docs from the app Postgres."""
    registry_rows = [
        {"server_id": sid}
        for sid in db.execute(select(McpServerRegistry.server_id)).scalars().all()
        if sid
    ]
    corpus_rows = [
        {
            "server_id": doc.server_id,
            "snippet": doc.snippet,
            "terms": doc.terms,
            "content_hash": doc.content_hash,
        }
        for doc in db.execute(select(AskCorpusDoc)).scalars().all()
    ]
    return registry_rows, corpus_rows


class TermCount(BaseModel):
    term: str
    count: int


class TermCoverageReport(BaseModel):
    servers_total: int
    servers_with_docs: int
    coverage_pct: float
    mean_terms_per_doc: float
    median_terms_per_doc: float
    empty_term_docs: int
    top_terms: List[TermCount] = []
    markdown: Optional[str] = None


@router.get("/ask/corpus/term-coverage-report", response_model=TermCoverageReport)
def term_coverage_report(db: Session = Depends(get_session)) -> TermCoverageReport:
    """Coverage of the registry by the ask corpus + term-vector richness."""
    registry_rows, corpus_rows = load_rows(db)
    report = build_report(registry_rows, corpus_rows)
    return TermCoverageReport(**report, markdown=render_markdown(report))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    registry_rows = [{"server_id": f"srv-{i}"} for i in range(1, 6)]
    corpus_rows = [
        {"server_id": "srv-1", "snippet": "a",
         "terms": ["alpha", "beta"], "content_hash": "h1"},
        {"server_id": "srv-2", "snippet": "b",
         "terms": {"name": ["alpha", "gamma"]}, "content_hash": "h2"},
        {"server_id": "srv-3", "snippet": "c",
         "terms": ["delta"], "content_hash": "h3"},
        {"server_id": "srv-4", "snippet": "d",
         "terms": [], "content_hash": "h4"},
    ]
    report = build_report(registry_rows, corpus_rows)
    assert report["servers_total"] == 5, report
    assert report["servers_with_docs"] == 4, report
    assert report["coverage_pct"] == 80.0, report
    assert report["empty_term_docs"] == 1, report
    assert isinstance(report["median_terms_per_doc"], float), report
    assert isinstance(report["mean_terms_per_doc"], float), report
    assert report["top_terms"][0]["term"] == "alpha", report
    md = render_markdown(report)
    assert "coverage" in md.lower(), md

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for i in range(1, 6):
        s.add(McpServerRegistry(server_id=f"srv-{i}", name=f"Server {i}",
                                registry_source="github",
                                url=f"https://github.com/acme/server-{i}"))
    for i, (sid, terms) in enumerate((("srv-1", {"name": ["alpha", "beta"]}),
                                      ("srv-2", ["alpha", "gamma"]),
                                      ("srv-3", ["delta"]),
                                      ("srv-4", [])), start=1):
        s.add(AskCorpusDoc(server_id=sid, snippet=f"snippet {sid}", terms=terms,
                           content_hash=f"h{i}"))
    s.commit()
    s.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    test_app.dependency_overrides[get_session] = _override_session
    client = TestClient(test_app)
    r = client.get("/api/ask/corpus/term-coverage-report")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["servers_total"] == 5, j
    assert j["servers_with_docs"] == 4, j
    assert j["coverage_pct"] == 80.0, j
    assert j["empty_term_docs"] == 1, j
    assert "coverage" in j["markdown"].lower(), j
    print("PASS")
