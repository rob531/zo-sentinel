from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

router = APIRouter(prefix="/api")


class StalestEntry(BaseModel):
    server_id: str
    indexed_at: datetime | None


class CorpusGapScanResponse(BaseModel):
    generated_at: datetime
    registry_servers: int
    indexed_servers: int
    coverage_ratio: float
    missing_sample: list[str]
    stalest: list[StalestEntry]


@router.get("/ask/corpus-gap-scan", response_model=CorpusGapScanResponse)
def scan_corpus_gaps(session: Session = Depends(get_session)) -> CorpusGapScanResponse:
    summary_sql = text(
        """
        SELECT
            COUNT(*) AS registry_servers,
            COALESCE(
                SUM(CASE WHEN indexed_rows > 0 THEN 1 ELSE 0 END),
                0
            ) AS indexed_servers
        FROM (
            SELECT
                registry.server_id,
                COUNT(corpus.server_id) AS indexed_rows
            FROM mcp_server_registry AS registry
            LEFT JOIN ask_corpus_index AS corpus
                ON corpus.server_id = registry.server_id
            GROUP BY registry.server_id
        ) AS coverage
        """
    )
    missing_sql = text(
        """
        SELECT registry.server_id
        FROM mcp_server_registry AS registry
        LEFT JOIN ask_corpus_index AS corpus
            ON corpus.server_id = registry.server_id
        GROUP BY registry.server_id
        HAVING COUNT(corpus.server_id) = 0
        ORDER BY registry.server_id ASC
        LIMIT :missing_limit
        """
    )
    stalest_sql = text(
        """
        SELECT corpus.server_id, corpus.indexed_at
        FROM ask_corpus_index AS corpus
        ORDER BY corpus.indexed_at ASC, corpus.server_id ASC
        LIMIT :stalest_limit
        """
    )

    summary = session.execute(summary_sql).mappings().one()
    registry_servers = int(summary["registry_servers"])
    indexed_servers = int(summary["indexed_servers"])

    missing_sample = [
        str(row["server_id"])
        for row in session.execute(
            missing_sql, {"missing_limit": 50}
        ).mappings()
    ]
    stalest = [
        StalestEntry(
            server_id=str(row["server_id"]),
            indexed_at=row["indexed_at"],
        )
        for row in session.execute(
            stalest_sql, {"stalest_limit": 20}
        ).mappings()
    ]

    coverage_ratio = (
        indexed_servers / registry_servers if registry_servers else 0.0
    )

    return CorpusGapScanResponse(
        generated_at=datetime.now(timezone.utc),
        registry_servers=registry_servers,
        indexed_servers=indexed_servers,
        coverage_ratio=coverage_ratio,
        missing_sample=missing_sample,
        stalest=stalest,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.metadata.create_all(
        test_engine,
        tables=[McpServerRegistry.__table__, AskCorpusDoc.__table__],
    )
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        for index in range(4):
            test_session.add(
                McpServerRegistry(server_id=f"server-{index + 1}")
            )
        test_session.add(
            AskCorpusDoc(
                server_id="server-1",
                snippet="Example corpus document",
                terms="example corpus",
                content_hash="content-hash-1",
                indexed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/ask/corpus-gap-scan")
        assert response.status_code == 200
        payload = response.json()
        assert payload["registry_servers"] == 4
        assert payload["indexed_servers"] == 1
        assert payload["coverage_ratio"] == 0.25
        assert len(payload["missing_sample"]) == 3

    test_engine.dispose()
    print("PASS")