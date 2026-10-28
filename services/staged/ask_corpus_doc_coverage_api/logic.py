from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

router = APIRouter(prefix="/api", tags=["ask-corpus"])


class CoveragePerSource(BaseModel):
    registry_source: str
    docs: int
    servers: int


class CoverageServer(BaseModel):
    server_id: str
    docs: int


class CorpusCoverageResponse(BaseModel):
    total_docs: int
    servers_indexed: int
    servers_total: int
    coverage_pct: float
    oldest_indexed_at: Optional[str]
    newest_indexed_at: Optional[str]
    per_source: List[CoveragePerSource]
    uncapped_top_servers: List[CoverageServer]


def _iso8601(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@router.get(
    "/ask/corpus/coverage",
    response_model=CorpusCoverageResponse,
)
def get_corpus_coverage(
    session: Session = Depends(get_session),
) -> CorpusCoverageResponse:
    total_docs, servers_indexed, oldest_indexed_at, newest_indexed_at = (
        session.query(
            func.count(AskCorpusDoc.server_id),
            func.count(distinct(AskCorpusDoc.server_id)),
            func.min(AskCorpusDoc.indexed_at),
            func.max(AskCorpusDoc.indexed_at),
        )
        .select_from(AskCorpusDoc)
        .one()
    )

    servers_total = (
        session.query(func.count(distinct(McpServerRegistry.server_id)))
        .select_from(McpServerRegistry)
        .scalar()
        or 0
    )

    coverage_pct = (
        round((float(servers_indexed) / float(servers_total)) * 100.0, 2)
        if servers_total
        else 0.0
    )

    source_rows = (
        session.query(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count(AskCorpusDoc.server_id).label("docs"),
            func.count(distinct(McpServerRegistry.server_id)).label("servers"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            AskCorpusDoc,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
        .all()
    )

    server_rows = (
        session.query(
            AskCorpusDoc.server_id.label("server_id"),
            func.count(AskCorpusDoc.server_id).label("docs"),
        )
        .select_from(AskCorpusDoc)
        .group_by(AskCorpusDoc.server_id)
        .order_by(
            func.count(AskCorpusDoc.server_id).desc(),
            AskCorpusDoc.server_id.asc(),
        )
        .all()
    )

    return CorpusCoverageResponse(
        total_docs=int(total_docs or 0),
        servers_indexed=int(servers_indexed or 0),
        servers_total=int(servers_total),
        coverage_pct=coverage_pct,
        oldest_indexed_at=_iso8601(oldest_indexed_at),
        newest_indexed_at=_iso8601(newest_indexed_at),
        per_source=[
            CoveragePerSource(
                registry_source=str(row.registry_source),
                docs=int(row.docs or 0),
                servers=int(row.servers or 0),
            )
            for row in source_rows
        ],
        uncapped_top_servers=[
            CoverageServer(
                server_id=str(row.server_id),
                docs=int(row.docs or 0),
            )
            for row in server_rows
        ],
    )


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    content_hash VARCHAR PRIMARY KEY,
                    indexed_at DATETIME,
                    server_id VARCHAR,
                    snippet TEXT,
                    terms TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    registry_source VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, registry_source, risk_tier)
                VALUES
                    ('server-1', 'registry-a', 'low'),
                    ('server-2', 'registry-b', 'medium'),
                    ('server-3', 'registry-a', 'high')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (content_hash, indexed_at, server_id, snippet, terms)
                VALUES
                    ('hash-1', '2024-01-01T00:00:00+00:00', 'server-1', 'one', 'alpha'),
                    ('hash-2', '2024-01-02T00:00:00+00:00', 'server-1', 'two', 'beta'),
                    ('hash-3', '2024-01-03T00:00:00+00:00', 'server-2', 'three', 'gamma'),
                    ('hash-4', '2024-01-04T00:00:00+00:00', 'server-2', 'four', 'delta')
                """
            )
        )

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_docs"] == 4
    assert payload["servers_indexed"] == 2
    assert payload["servers_total"] == 3
    assert payload["coverage_pct"] == 66.67
    assert sum(row["docs"] for row in payload["per_source"]) == payload["total_docs"]
    print("PASS")