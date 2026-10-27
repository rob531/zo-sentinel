from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_reasoning_gaps

router = APIRouter(
    prefix="/api",
    tags=["verdict-reasoning-gap-api"],
)


class ConfidenceBandBreakdown(BaseModel):
    null: int = Field(ge=0)
    low: int = Field(ge=0)
    mid: int = Field(ge=0)
    high: int = Field(ge=0)


class VerdictReasoningGap(BaseModel):
    verdict: str
    total: int = Field(ge=0)
    missing_reasoning: int = Field(ge=0)
    reasoning_coverage_pct: float = Field(ge=0.0, le=100.0)
    confidence_bands: ConfidenceBandBreakdown


class VerdictReasoningGapTotals(BaseModel):
    servers: int = Field(ge=0)
    missing_reasoning: int = Field(ge=0)


class VerdictReasoningGapResponse(BaseModel):
    verdicts: list[VerdictReasoningGap]
    totals: VerdictReasoningGapTotals


@router.get(
    "/registry/verdict-reasoning-gaps",
    response_model=VerdictReasoningGapResponse,
)
def verdict_reasoning_gaps(
    session: Session = Depends(get_session),
) -> VerdictReasoningGapResponse:
    return get_verdict_reasoning_gaps(session)


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="server-1",
                name="Server One",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="Documented rationale",
                confidence=0.9,
            ),
            McpServerRegistry(
                server_id="server-2",
                name="Server Two",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="Additional rationale",
                confidence=0.6,
            ),
            McpServerRegistry(
                server_id="server-3",
                name="Server Three",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning="",
                confidence=0.4,
            ),
            McpServerRegistry(
                server_id="server-4",
                name="Server Four",
                verdict="TRUSTED_GENERAL",
                verdict_reasoning=None,
                confidence=None,
            ),
        ]
    )
    test_db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/verdict-reasoning-gaps")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 4
    assert payload["totals"]["missing_reasoning"] == 2
    assert payload["verdicts"][0]["verdict"] == "TRUSTED_GENERAL"
    assert payload["verdicts"][0]["total"] == 4
    assert payload["verdicts"][0]["missing_reasoning"] == 2
    assert payload["verdicts"][0]["reasoning_coverage_pct"] == 50.0
    assert payload["verdicts"][0]["confidence_bands"] == {
        "null": 1,
        "low": 1,
        "mid": 1,
        "high": 1,
    }
    print("PASS")