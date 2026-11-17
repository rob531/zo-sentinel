# services/staged/registry_verdict_trust_agreement_api/contract.py

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import Dict, List

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class MismatchItem(BaseModel):
    server_id: str
    name: str | None = None
    verdict: str | None = None
    trust_score: float | None = None
    risk_tier: str | None = None
    class_name: str


class VerdictTrustAgreementResponse(BaseModel):
    total: int
    classes: Dict[str, int]
    mismatches: List[MismatchItem]
    verdict_counts: Dict[str, int]
    mismatch_rate: float


def _classify(row: McpServerRegistry) -> str:
    verdict = (row.verdict or "").upper()
    trust = row.trust_score or 0

    if verdict.startswith("TRUSTED"):
        return "trusted_agreement" if trust >= 60 else "trusted_mismatch"
    if verdict in ("HIGH_RISK_ISOLATED", "KNOWN_THREAT"):
        return "highrisk_agreement" if trust < 60 else "highrisk_mismatch"
    # other verdicts – use the verdict value itself as class name
    return f"verdict_{verdict.lower()}" if verdict else "verdict_unknown"


@router.get(
    "/registry/verdict-trust-agreement",
    response_model=VerdictTrustAgreementResponse,
)
def get_verdict_trust_agreement(session: Session = Depends(get_session)):
    rows = session.query(McpServerRegistry).all()

    total = len(rows)
    class_counts: Dict[str, int] = {}
    verdict_counts: Dict[str, int] = {}
    mismatches: List[MismatchItem] = []

    for row in rows:
        class_name = _classify(row)
        class_counts[class_name] = class_counts.get(class_name, 0) + 1

        verdict_key = (row.verdict or "").upper()
        verdict_counts[verdict_key] = verdict_counts.get(verdict_key, 0) + 1

        if class_name in ("trusted_mismatch", "highrisk_mismatch"):
            mismatches.append(
                MismatchItem(
                    server_id=row.server_id,
                    name=row.name,
                    verdict=row.verdict,
                    trust_score=row.trust_score,
                    risk_tier=row.risk_tier,
                    class_name=class_name,
                )
            )

    mismatch_rate = len(mismatches) / total if total else 0.0

    return VerdictTrustAgreementResponse(
        total=total,
        classes=class_counts,
        mismatches=mismatches,
        verdict_counts=verdict_counts,
        mismatch_rate=mismatch_rate,
    )


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_verdict_trust_agreement_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite DB with the real models
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Seed data covering the five classification groups
    seed_rows = [
        # trusted_agreement
        McpServerRegistry(
            server_id="srv_a",
            name="TrustedAgree",
            verdict="TRUSTED_GOOD",
            trust_score=80,
            risk_tier="low",
        ),
        # trusted_mismatch
        McpServerRegistry(
            server_id="srv_b",
            name="TrustedMismatch",
            verdict="TRUSTED_GOOD",
            trust_score=50,
            risk_tier="medium",
        ),
        # highrisk_mismatch
        McpServerRegistry(
            server_id="srv_c",
            name="HighRiskMismatch",
            verdict="HIGH_RISK_ISOLATED",
            trust_score=80,
            risk_tier="high",
        ),
        # highrisk_agreement
        McpServerRegistry(
            server_id="srv_d",
            name="HighRiskAgree",
            verdict="KNOWN_THREAT",
            trust_score=50,
            risk_tier="high",
        ),
        # other verdict 1
        McpServerRegistry(
            server_id="srv_e",
            name="OtherOne",
            verdict="UNKNOWN",
            trust_score=70,
            risk_tier="low",
        ),
        # other verdict 2 (different verdict to ensure separate counting)
        McpServerRegistry(
            server_id="srv_f",
            name="OtherTwo",
            verdict="SUSPICIOUS",
            trust_score=30,
            risk_tier="medium",
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(seed_rows)
        db.commit()

    # Override the real get_session dependency with our test session factory
    app = create_app()
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    client = TestClient(app)
    resp = client.get("/api/registry/verdict-trust-agreement")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Assertions per acceptance criteria
    assert data["total"] == 6, "total mismatch"
    mismatched_ids = {m["server_id"] for m in data["mismatches"]}
    expected_mismatches = {"srv_b", "srv_c"}
    assert mismatched_ids == expected_mismatches, "mismatch list incorrect"

    expected_rate = len(expected_mismatches) / 6
    assert abs(data["mismatch_rate"] - expected_rate) < 1e-6, "mismatch_rate incorrect"

    total_verdict_counts = sum(data["verdict_counts"].values())
    assert total_verdict_counts == data["total"], "verdict_counts sum mismatch"

    print("PASS")
    raise SystemExit(0)