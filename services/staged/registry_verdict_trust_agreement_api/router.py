from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_verdict_trust_agreement

router = APIRouter(prefix="/api", tags=["registry"])


class VerdictTrustAgreementMismatch(BaseModel):
    server_id: str | None = None
    name: str | None = None
    verdict: str | None = None
    trust_score: float | None = None
    risk_tier: str | None = None
    class_name: str


class VerdictTrustAgreementResponse(BaseModel):
    total: int
    classes: dict[str, int] = Field(default_factory=dict)
    mismatches: list[VerdictTrustAgreementMismatch] = Field(default_factory=list)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    mismatch_rate: float


@router.get(
    "/registry/verdict-trust-agreement",
    response_model=VerdictTrustAgreementResponse,
    operation_id="get_registry_verdict_trust_agreement",
)
async def get_registry_verdict_trust_agreement(
    session: Session = Depends(get_session),
) -> VerdictTrustAgreementResponse:
    result: Any = get_verdict_trust_agreement(session)
    if inspect.isawaitable(result):
        result = await result
    return VerdictTrustAgreementResponse.model_validate(result)


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=test_engine)

    rows = [
        {
            "server_id": "trusted-high",
            "name": "Trusted High",
            "verdict": "TRUSTED",
            "trust_score": 80,
            "risk_tier": "LOW",
        },
        {
            "server_id": "trusted-low",
            "name": "Trusted Low",
            "verdict": "TRUSTED",
            "trust_score": 40,
            "risk_tier": "MEDIUM",
        },
        {
            "server_id": "isolated-high",
            "name": "Isolated High",
            "verdict": "HIGH_RISK_ISOLATED",
            "trust_score": 75,
            "risk_tier": "HIGH",
        },
        {
            "server_id": "threat-low",
            "name": "Threat Low",
            "verdict": "KNOWN_THREAT",
            "trust_score": 30,
            "risk_tier": "HIGH",
        },
        {
            "server_id": "other-one",
            "name": "Other One",
            "verdict": "UNVERIFIED",
            "trust_score": 50,
            "risk_tier": "MEDIUM",
        },
        {
            "server_id": "other-two",
            "name": "Other Two",
            "verdict": "UNVERIFIED",
            "trust_score": 90,
            "risk_tier": "LOW",
        },
    ]

    with TestSession() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, trust_score, risk_tier)
                VALUES
                    (:server_id, :name, :verdict, :trust_score, :risk_tier)
                """
            ),
            rows,
        )
        session.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-trust-agreement")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 6
        assert sum(payload["verdict_counts"].values()) == payload["total"]
        assert payload["mismatch_rate"] == len(payload["mismatches"]) / payload["total"]

        mismatch_ids = {item["server_id"] for item in payload["mismatches"]}
        assert mismatch_ids <= {"trusted-low", "isolated-high"}

    print("PASS")