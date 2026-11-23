"""HTTP surface for verdict reasoning quality auditing."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_reasoning_quality

router = APIRouter(prefix="/api/quality", tags=["quality"])


class VerdictReasoningBucket(BaseModel):
    verdict: str
    servers: int
    missing_reasoning: int
    hollow_reasoning: int
    avg_reasoning_length: float
    missing_confidence: int


class VerdictReasoningQualityResponse(BaseModel):
    total_servers: int
    by_verdict: list[VerdictReasoningBucket]


@router.get(
    "/verdict-reasoning",
    response_model=VerdictReasoningQualityResponse,
)
def verdict_reasoning_quality(
    db: Session = Depends(get_session),
) -> VerdictReasoningQualityResponse:
    result = get_verdict_reasoning_quality(db)
    return VerdictReasoningQualityResponse(**result)


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSession()
    db.add_all(
        [
            McpServerRegistry(
                server_id="quality-1",
                verdict="approved",
                verdict_reasoning=(
                    "This server has a sufficiently detailed rationale explaining "
                    "why the assessed verdict is appropriate."
                ),
                confidence=0.9,
            ),
            McpServerRegistry(
                server_id="quality-2",
                verdict="flagged",
                verdict_reasoning="",
                confidence=0.5,
            ),
            McpServerRegistry(
                server_id="quality-3",
                verdict="flagged",
                verdict_reasoning="short",
                confidence=0.4,
            ),
            McpServerRegistry(
                server_id="quality-4",
                verdict="neutral",
                verdict_reasoning=(
                    "The assessment is complete and includes enough context to "
                    "support the selected verdict."
                ),
                confidence=None,
            ),
        ]
    )
    db.commit()

    def override_get_session():
        yield db

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/quality/verdict-reasoning")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4

    flagged = next(
        bucket for bucket in payload["by_verdict"] if bucket["verdict"] == "flagged"
    )
    assert flagged["missing_reasoning"] == 1
    assert flagged["hollow_reasoning"] >= 1

    print("PASS")