from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_index_coverage

router = APIRouter()


class CorpusCoverageResponse(BaseModel):
    indexed_servers: int
    total_servers: int
    coverage_pct: float
    stale_index_count: int
    oldest_indexed_at: Optional[str]
    missing_count: int
    missing_sample: List[str]


@router.get(
    "/api/ask-corpus/coverage",
    response_model=CorpusCoverageResponse,
)
def get_corpus_index_coverage(
    session: Session = Depends(get_session),
) -> CorpusCoverageResponse:
    coverage = get_index_coverage(session)
    return CorpusCoverageResponse(
        indexed_servers=coverage["indexed_servers"],
        total_servers=coverage["total_servers"],
        coverage_pct=coverage["coverage_pct"],
        stale_index_count=coverage["stale_index_count"],
        oldest_indexed_at=coverage["oldest_indexed_at"],
        missing_count=coverage["missing_count"],
        missing_sample=coverage["missing_sample"],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import AskCorpusDoc, McpServerRegistry

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    AskCorpusDoc.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)

    test_session = TestingSessionLocal()
    test_session.add_all(
        [
            McpServerRegistry(server_id="server-1", name="Server 1", risk_tier="low"),
            McpServerRegistry(server_id="server-2", name="Server 2", risk_tier="medium"),
            McpServerRegistry(server_id="server-3", name="Server 3", risk_tier="high"),
            McpServerRegistry(server_id="server-4", name="Server 4", risk_tier="critical"),
            AskCorpusDoc(
                server_id="server-1",
                snippet="one",
                terms="one",
                content_hash="hash-1",
            ),
            AskCorpusDoc(
                server_id="server-2",
                snippet="two",
                terms="two",
                content_hash="hash-2",
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask-corpus/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["indexed_servers"] == 2
    assert payload["total_servers"] == 4
    assert payload["coverage_pct"] == 50.0
    assert payload["missing_count"] == 2
    assert len(payload["missing_sample"]) == 2
    print("PASS")