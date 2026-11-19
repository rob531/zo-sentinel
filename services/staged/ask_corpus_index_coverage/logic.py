from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def _normalise_tokens(
    query: Optional[str] = None,
    tokens: Optional[Sequence[str]] = None,
    token: Optional[str] = None,
) -> list[str]:
    values: list[str] = []
    if tokens is not None:
        values.extend(str(item) for item in tokens)
    if token:
        values.append(str(token))
    if query:
        values.extend(part for part in query.replace(",", " ").split() if part)

    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip().lower()
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _term_count(terms: Any) -> int:
    return len(terms) if isinstance(terms, list) else 0


def _age_days(indexed_at: Optional[datetime], now: datetime) -> int:
    if indexed_at is None:
        return 0
    if indexed_at.tzinfo is None:
        indexed_at = indexed_at.replace(tzinfo=timezone.utc)
    return max(0, (now - indexed_at).days)


def compute_coverage(
    db: Session,
    query_tokens: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compute registry corpus coverage using the real application models."""
    now = datetime.now(timezone.utc)
    tokens = _normalise_tokens(tokens=query_tokens)

    rows = db.execute(
        select(AskCorpusDoc, McpServerRegistry)
        .select_from(AskCorpusDoc)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == AskCorpusDoc.server_id,
        )
        .order_by(AskCorpusDoc.server_id)
    ).all()

    total_registry_servers = int(
        db.execute(select(func.count(McpServerRegistry.server_id))).scalar() or 0
    )

    matched_server_ids: set[str] = set()
    if tokens:
        terms_text = func.lower(cast(AskCorpusDoc.terms, String))
        conditions = []
        for token in tokens:
            escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(terms_text.like(f"%{escaped}%", escape="\\"))
        matched_rows = db.execute(
            select(func.distinct(AskCorpusDoc.server_id)).where(or_(*conditions))
        ).all()
        matched_server_ids = {str(server_id) for server_id, in matched_rows}
    else:
        matched_server_ids = {str(doc.server_id) for doc, _ in rows}

    matches: list[Dict[str, Any]] = []
    empty_terms_rows = 0
    indexed_server_ids: set[str] = set()

    for doc, registry in rows:
        indexed_server_ids.add(str(doc.server_id))
        term_count = _term_count(doc.terms)
        if term_count == 0:
            empty_terms_rows += 1
        if str(doc.server_id) not in matched_server_ids:
            continue
        matches.append(
            {
                "server_id": doc.server_id,
                "name": registry.name if registry is not None else None,
                "risk_tier": registry.risk_tier if registry is not None else None,
                "indexed_at": doc.indexed_at,
                "age_days": _age_days(doc.indexed_at, now),
                "term_count": term_count,
            }
        )

    total_indexed = len(indexed_server_ids)
    matched_servers = len(matched_server_ids)
    coverage_ratio = (
        float(matched_servers / total_registry_servers)
        if total_registry_servers
        else 0.0
    )

    return {
        "total_indexed": total_indexed,
        "total_registry_servers": total_registry_servers,
        "coverage_ratio": coverage_ratio,
        "empty_terms_rows": empty_terms_rows,
        "matches": matches,
    }


def get_coverage(
    db: Session,
    query: Optional[str] = None,
    tokens: Optional[Sequence[str]] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility entry point accepting a query string or explicit tokens."""
    return compute_coverage(db, query_tokens=_normalise_tokens(query, tokens, token))


def corpus_coverage(
    db: Session,
    query_tokens: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return compute_coverage(db, query_tokens=query_tokens)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        for index, server_id in enumerate(("srv-1", "srv-2", "srv-3", "srv-4")):
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Server {index + 1}",
                    risk_tier="low" if index < 2 else "medium",
                )
            )
        session.add(
            AskCorpusDoc(
                server_id="srv-1",
                snippet="alpha documentation",
                terms=["alpha", "documentation"],
                content_hash="hash-1",
                indexed_at=datetime.now(timezone.utc) - timedelta(days=2),
            )
        )
        session.add(
            AskCorpusDoc(
                server_id="srv-2",
                snippet="empty documentation",
                terms=[],
                content_hash="hash-2",
                indexed_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/ask/corpus/coverage")
    def coverage_route(db: Session = Depends(get_session)):
        return get_coverage(db)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session
    response = TestClient(test_app).get("/api/ask/corpus/coverage")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_indexed"] == 2
    assert payload["total_registry_servers"] == 4
    assert payload["coverage_ratio"] == 0.5
    assert payload["empty_terms_rows"] == 1
    assert len(payload["matches"]) == 2
    assert all("term_count" in item for item in payload["matches"])
    assert all("age_days" in item for item in payload["matches"])
    print("PASS")