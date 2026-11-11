from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


_TOP_GAPS_LIMIT = 50


def get_corpus_gap(db: Session = Depends(get_session)) -> Dict[str, Any]:
    total_registry_servers = int(
        db.execute(
            text("SELECT COUNT(*) AS total FROM mcp_server_registry")
        ).scalar()
        or 0
    )

    index_stats = db.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT server_id) AS indexed_servers,
                MIN(indexed_at) AS oldest_indexed_at,
                MAX(indexed_at) AS newest_indexed_at
            FROM ask_corpus_index
            """
        )
    ).mappings().one()

    source_rows = db.execute(
        text(
            """
            SELECT
                COALESCE(r.registry_source, 'unknown') AS registry_source,
                COUNT(*) AS count
            FROM mcp_server_registry AS r
            LEFT JOIN (
                SELECT DISTINCT server_id
                FROM ask_corpus_index
            ) AS indexed
                ON indexed.server_id = r.server_id
            WHERE indexed.server_id IS NULL
            GROUP BY COALESCE(r.registry_source, 'unknown')
            ORDER BY registry_source
            """
        )
    ).mappings().all()

    top_gap_rows = db.execute(
        text(
            """
            SELECT
                r.server_id,
                r.name,
                r.registry_source,
                r.risk_tier,
                r.last_seen
            FROM mcp_server_registry AS r
            LEFT JOIN (
                SELECT DISTINCT server_id
                FROM ask_corpus_index
            ) AS indexed
                ON indexed.server_id = r.server_id
            WHERE indexed.server_id IS NULL
            ORDER BY
                CASE LOWER(COALESCE(r.risk_tier, ''))
                    WHEN 'critical' THEN 1
                    WHEN 'severe' THEN 2
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 4
                    WHEN 'moderate' THEN 5
                    WHEN 'low' THEN 6
                    WHEN 'minimal' THEN 7
                    WHEN 'informational' THEN 8
                    WHEN 'info' THEN 9
                    WHEN 'unknown' THEN 10
                    ELSE 11
                END,
                CASE WHEN r.last_seen IS NULL THEN 1 ELSE 0 END,
                r.last_seen DESC,
                r.server_id
            LIMIT :limit
            """
        ),
        {"limit": _TOP_GAPS_LIMIT},
    ).mappings().all()

    by_registry_source = {
        str(row["registry_source"]): int(row["count"])
        for row in source_rows
    }
    gap_count = sum(by_registry_source.values())

    return {
        "total_registry_servers": total_registry_servers,
        "indexed_servers": int(index_stats["indexed_servers"] or 0),
        "gap_servers": gap_count,
        "gap_count": gap_count,
        "by_registry_source": by_registry_source,
        "oldest_indexed_at": index_stats["oldest_indexed_at"],
        "newest_indexed_at": index_stats["newest_indexed_at"],
        "top_gaps": [dict(row) for row in top_gap_rows],
    }


get_corpus_gap_summary = get_corpus_gap
get_uncovered_servers = get_corpus_gap
get_corpus_uncovered_servers = get_corpus_gap


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    AskCorpusDoc.__table__.create(engine)

    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    indexed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-indexed",
                    name="Indexed Server",
                    registry_source="catalog",
                    risk_tier="medium",
                    last_seen=indexed_at,
                ),
                McpServerRegistry(
                    server_id="server-gap-critical",
                    name="Critical Gap",
                    registry_source="catalog",
                    risk_tier="critical",
                    last_seen=indexed_at,
                ),
                McpServerRegistry(
                    server_id="server-gap-high",
                    name="High Gap",
                    registry_source="discovery",
                    risk_tier="high",
                    last_seen=indexed_at,
                ),
            ]
        )
        session.add(
            AskCorpusDoc(
                server_id="server-indexed",
                snippet="Indexed content",
                terms="indexed content",
                content_hash="content-hash-indexed",
                indexed_at=indexed_at,
            )
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/ask/corpus-gap")
    def corpus_gap_endpoint(payload: Dict[str, Any] = Depends(get_corpus_gap)):
        return payload

    with TestClient(local_app) as client:
        response = client.get("/api/ask/corpus-gap")
        assert response.status_code == 200
        payload = response.json()
        assert payload["gap_count"] == 2
        assert payload["indexed_servers"] == 1
        assert payload["total_registry_servers"] == 3

    print("PASS")