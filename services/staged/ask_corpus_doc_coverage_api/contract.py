from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class PerSourceCoverage(BaseModel):
    registry_source: str
    docs: int
    servers: int


class ServerDocumentCount(BaseModel):
    server_id: str
    docs: int


class CorpusCoverageResponse(BaseModel):
    total_docs: int
    servers_indexed: int
    servers_total: int
    coverage_pct: float = Field(ge=0.0, le=100.0)
    oldest_indexed_at: Optional[str]
    newest_indexed_at: Optional[str]
    per_source: List[PerSourceCoverage]
    uncapped_top_servers: List[ServerDocumentCount]


app = FastAPI(title="Ask Corpus Document Coverage API")


def _as_iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return value
    else:
        return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


@app.get(
    "/api/ask/corpus/coverage",
    response_model=CorpusCoverageResponse,
)
def corpus_coverage(session: Session = Depends(get_session)) -> CorpusCoverageResponse:
    registry_rows = session.execute(
        select(
            McpServerRegistry.__table__.c.server_id,
            McpServerRegistry.__table__.c.registry_source,
        )
    ).mappings().all()

    corpus_totals = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total_docs,
                COUNT(DISTINCT server_id) AS servers_indexed,
                MIN(indexed_at) AS oldest_indexed_at,
                MAX(indexed_at) AS newest_indexed_at
            FROM ask_corpus_index
            """
        )
    ).mappings().one()

    server_document_rows = session.execute(
        text(
            """
            SELECT server_id, COUNT(*) AS docs
            FROM ask_corpus_index
            GROUP BY server_id
            """
        )
    ).mappings().all()

    registry_by_server: Dict[str, str] = {}
    source_order: List[str] = []
    for row in registry_rows:
        server_id = str(row["server_id"])
        source = str(row["registry_source"] or "unknown")
        registry_by_server[server_id] = source
        if source not in source_order:
            source_order.append(source)

    docs_by_source: Dict[str, int] = {source: 0 for source in source_order}
    servers_by_source: Dict[str, set] = {source: set() for source in source_order}

    server_counts: List[ServerDocumentCount] = []
    for row in server_document_rows:
        server_id = str(row["server_id"])
        docs = int(row["docs"] or 0)
        source = registry_by_server.get(server_id, "unknown")
        if source not in docs_by_source:
            docs_by_source[source] = 0
            servers_by_source[source] = set()
            source_order.append(source)
        docs_by_source[source] += docs
        servers_by_source[source].add(server_id)
        server_counts.append(ServerDocumentCount(server_id=server_id, docs=docs))

    server_counts.sort(key=lambda item: (-item.docs, item.server_id))

    servers_total = len(registry_by_server)
    servers_indexed = int(corpus_totals["servers_indexed"] or 0)
    total_docs = int(corpus_totals["total_docs"] or 0)
    coverage_pct = (
        round((servers_indexed / servers_total) * 100.0, 2)
        if servers_total
        else 0.0
    )

    per_source = [
        PerSourceCoverage(
            registry_source=source,
            docs=docs_by_source[source],
            servers=len(servers_by_source[source]),
        )
        for source in sorted(source_order)
    ]

    return CorpusCoverageResponse(
        total_docs=total_docs,
        servers_indexed=servers_indexed,
        servers_total=servers_total,
        coverage_pct=coverage_pct,
        oldest_indexed_at=_as_iso8601(corpus_totals["oldest_indexed_at"]),
        newest_indexed_at=_as_iso8601(corpus_totals["newest_indexed_at"]),
        per_source=per_source,
        uncapped_top_servers=server_counts,
    )


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    registry_source VARCHAR NOT NULL,
                    risk_tier VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id VARCHAR NOT NULL,
                    snippet TEXT NOT NULL,
                    terms TEXT NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    indexed_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, registry_source, risk_tier)
                VALUES
                    (:server_id, :registry_source, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-alpha-1",
                    "registry_source": "alpha",
                    "risk_tier": "low",
                },
                {
                    "server_id": "server-alpha-2",
                    "registry_source": "alpha",
                    "risk_tier": "high",
                },
                {
                    "server_id": "server-beta-1",
                    "registry_source": "beta",
                    "risk_tier": "medium",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    (:server_id, :snippet, :terms, :content_hash, :indexed_at)
                """
            ),
            [
                {
                    "server_id": "server-alpha-1",
                    "snippet": "Alpha document one",
                    "terms": "alpha document one",
                    "content_hash": "hash-alpha-1",
                    "indexed_at": "2024-01-01T00:00:00Z",
                },
                {
                    "server_id": "server-alpha-1",
                    "snippet": "Alpha document two",
                    "terms": "alpha document two",
                    "content_hash": "hash-alpha-2",
                    "indexed_at": "2024-01-02T00:00:00Z",
                },
                {
                    "server_id": "server-alpha-2",
                    "snippet": "Alpha document three",
                    "terms": "alpha document three",
                    "content_hash": "hash-alpha-3",
                    "indexed_at": "2024-01-03T00:00:00Z",
                },
                {
                    "server_id": "server-alpha-2",
                    "snippet": "Alpha document four",
                    "terms": "alpha document four",
                    "content_hash": "hash-alpha-4",
                    "indexed_at": "2024-01-04T00:00:00Z",
                },
            ],
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/ask/corpus/coverage")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_docs"] == 4, payload
            assert payload["servers_indexed"] == 2, payload
            assert payload["servers_total"] == 3, payload
            assert payload["coverage_pct"] == 66.67, payload
            assert sum(item["docs"] for item in payload["per_source"]) == 4, payload
            assert sum(item["servers"] for item in payload["per_source"]) == 2, payload
            assert payload["oldest_indexed_at"] is not None, payload
            assert payload["newest_indexed_at"] is not None, payload
            assert len(payload["uncapped_top_servers"]) == 2, payload
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()