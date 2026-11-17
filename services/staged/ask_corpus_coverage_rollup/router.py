from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry
from .logic import get_coverage_rollup


router = APIRouter(prefix="/api", tags=["ask-corpus-coverage-rollup"])


class CorpusCoverageGap(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_indexed: Optional[datetime] = None


class AskCorpusCoverageResponse(BaseModel):
    total_servers: int
    indexed_servers: int
    coverage_pct: float
    stale_index_count: int
    by_risk_tier: Dict[str, int]
    top_gaps: List[CorpusCoverageGap]


@router.get(
    "/ask/corpus/coverage",
    response_model=AskCorpusCoverageResponse,
    summary="Get corpus coverage for registered MCP servers",
)
def ask_corpus_coverage(
    session: Session = Depends(get_session),
) -> AskCorpusCoverageResponse:
    return get_coverage_rollup(session)


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.execute(
            insert(McpServerRegistry),
            [
                {
                    "server_id": "server-indexed-1",
                    "name": "Indexed One",
                    "risk_tier": "low",
                    "verdict": "approved",
                },
                {
                    "server_id": "server-indexed-2",
                    "name": "Indexed Two",
                    "risk_tier": "high",
                    "verdict": "approved",
                },
                {
                    "server_id": "server-unindexed",
                    "name": "Unindexed Server",
                    "risk_tier": "medium",
                    "verdict": "pending",
                },
            ],
        )
        session.execute(
            insert(AskCorpusDoc),
            [
                {
                    "server_id": "server-indexed-1",
                    "snippet": "first corpus document",
                    "terms": "first",
                    "content_hash": "hash-1",
                    "indexed_at": now,
                },
                {
                    "server_id": "server-indexed-2",
                    "snippet": "second corpus document",
                    "terms": "second",
                    "content_hash": "hash-2",
                    "indexed_at": now - timedelta(days=31),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = router and __import__("fastapi").FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus/coverage")
    payload = response.json()
    assert response.status_code == 200, payload
    assert payload["total_servers"] == 3, payload
    assert payload["indexed_servers"] == 2, payload
    assert 60 <= payload["coverage_pct"] <= 70, payload
    assert any(
        gap["server_id"] == "server-unindexed" for gap in payload["top_gaps"]
    ), payload
    print("PASS")