from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TierStat(BaseModel):
    tier: str
    count: int
    avg_trust: Optional[float]
    min_trust: Optional[float]
    max_trust: Optional[float]


class TierThreshold(BaseModel):
    tier: str
    min_score: float
    max_score: float


class TierPolicyResponse(BaseModel):
    tiers: List[TierStat]
    thresholds: List[TierThreshold]
    total_scored: int
    unscored: int


THRESHOLDS = [
    {"tier": "TRUSTED_GENERAL", "min_score": 0.8, "max_score": 1.0},
    {"tier": "TRUSTED_RESEARCH", "min_score": 0.6, "max_score": 0.8},
    {"tier": "ENTERPRISE_CONTROLLED", "min_score": 0.4, "max_score": 0.6},
    {"tier": "CAUTION_LIMITED", "min_score": 0.2, "max_score": 0.4},
    {"tier": "HIGH_RISK_ISOLATED", "min_score": 0.0, "max_score": 0.2},
    {"tier": "INSUFFICIENT", "min_score": 0.0, "max_score": 0.0},
]
_THRESHOLD_ORDER = {item["tier"]: index for index, item in enumerate(THRESHOLDS)}


def get_tier_policy(session: Session = Depends(get_session)) -> TierPolicyResponse:
    statement = (
        select(
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count(McpServerRegistry.server_id).label("n"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust"),
            func.min(McpServerRegistry.trust_score).label("min_trust"),
            func.max(McpServerRegistry.trust_score).label("max_trust"),
        )
        .where(McpServerRegistry.risk_tier.is_not(None))
        .group_by(McpServerRegistry.risk_tier)
    )

    rows = session.execute(statement).all()
    tiers = [
        TierStat(
            tier=row.risk_tier,
            count=int(row.n),
            avg_trust=float(row.avg_trust) if row.avg_trust is not None else None,
            min_trust=float(row.min_trust) if row.min_trust is not None else None,
            max_trust=float(row.max_trust) if row.max_trust is not None else None,
        )
        for row in rows
    ]
    tiers.sort(key=lambda item: (_THRESHOLD_ORDER.get(item.tier, len(_THRESHOLD_ORDER)), item.tier))

    total_scored = session.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.risk_tier.is_not(None)
        )
    ).scalar_one()
    unscored = session.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.risk_tier.is_(None)
        )
    ).scalar_one()

    return TierPolicyResponse(
        tiers=tiers,
        thresholds=[TierThreshold(**threshold) for threshold in THRESHOLDS],
        total_scored=int(total_scored),
        unscored=int(unscored),
    )


router = APIRouter(prefix="/api")


@router.get("/risk/tier-policy", response_model=TierPolicyResponse)
def tier_policy(session: Session = Depends(get_session)) -> TierPolicyResponse:
    return get_tier_policy(session)


app = FastAPI()
app.include_router(router)


def run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    test_session = Session(test_engine)

    test_session.add_all(
        [
            McpServerRegistry(
                server_id="registry-test-1",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.95,
            ),
            McpServerRegistry(
                server_id="registry-test-2",
                risk_tier="TRUSTED_GENERAL",
                trust_score=0.85,
            ),
            McpServerRegistry(
                server_id="registry-test-3",
                risk_tier="CAUTION_LIMITED",
                trust_score=0.35,
            ),
            McpServerRegistry(
                server_id="registry-test-4",
                risk_tier=None,
                trust_score=None,
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/risk/tier-policy")
            assert response.status_code == 200

            payload = response.json()
            trusted = next(
                tier for tier in payload["tiers"] if tier["tier"] == "TRUSTED_GENERAL"
            )
            assert trusted["count"] == 2
            assert payload["unscored"] == 1
            assert payload["thresholds"]
            assert [item["tier"] for item in payload["thresholds"]] == [
                item["tier"] for item in THRESHOLDS
            ]
    finally:
        test_session.close()
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()