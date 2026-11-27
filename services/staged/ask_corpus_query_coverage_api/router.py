"""HTTP surface for corpus query coverage reporting."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

from .logic import get_corpus_coverage

router = APIRouter(prefix="/api", tags=["ask-corpus-query-coverage"])


class MissingServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    verdict: str | None = None


class CoverageResponse(BaseModel):
    total_indexed: int
    total_registry: int
    coverage_ratio: float
    missing_sample: list[MissingServer]
    oldest_indexed_at: datetime | None = None
    newest_indexed_at: datetime | None = None
    indexed_last_7d: int


def _response_data(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "__dict__"):
        return vars(result)
    raise TypeError("Corpus coverage result must be a mapping or response model")


@router.get("/ask/coverage", response_model=CoverageResponse)
def get_coverage(
    sample: int = 20,
    db: Session = Depends(get_session),
) -> CoverageResponse:
    result = _response_data(get_corpus_coverage(db, sample))
    return CoverageResponse(
        total_indexed=result["total_indexed"],
        total_registry=result["total_registry"],
        coverage_ratio=result["coverage_ratio"],
        missing_sample=result["missing_sample"],
        oldest_indexed_at=result.get("oldest_indexed_at"),
        newest_indexed_at=result.get("newest_indexed_at"),
        indexed_last_7d=result["indexed_last_7d"],
    )


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        test_engine,
        tables=[AskCorpusDoc.__table__, McpServerRegistry.__table__],
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        for index in range(3):
            db.add(
                AskCorpusDoc(
                    server_id=f"covered-{index}",
                    snippet=f"Corpus document {index}",
                    terms="mcp security corpus",
                    content_hash=f"hash-{index}",
                    indexed_at=now - timedelta(days=index),
                )
            )
        for index in range(3):
            db.add(
                McpServerRegistry(
                    server_id=f"covered-{index}",
                    name=f"Covered Server {index}",
                    risk_tier="low",
                    verdict="approved",
                )
            )
        db.add(
            McpServerRegistry(
                server_id="uncovered-0",
                name="Uncovered Server",
                risk_tier="medium",
                verdict="review",
            )
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/coverage?sample=20")
    response.raise_for_status()
    payload = response.json()
    assert payload["total_indexed"] == 3
    assert payload["total_registry"] == 4
    assert abs(payload["coverage_ratio"] - 0.75) <= 0.01
    assert len(payload["missing_sample"]) == 1
    assert payload["missing_sample"][0]["server_id"] == "uncovered-0"
    print("PASS")