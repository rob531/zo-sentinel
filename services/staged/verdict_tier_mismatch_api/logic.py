from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, not_, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class VerdictTierMismatchRow(BaseModel):
    server_id: str
    name: Optional[str]
    verdict: Optional[str]
    risk_tier: Optional[str]
    trust_score: Optional[float]
    confidence: Optional[float]
    last_assessed: Optional[datetime]


class VerdictTierMismatchResponse(BaseModel):
    total: int
    counts: Dict[str, int]
    rows: List[VerdictTierMismatchRow]
    generated_at: datetime


TRUSTED_VERDICTS = ("TRUSTED_GENERAL", "TRUSTED_RESEARCH")
KNOWN_VERDICTS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)


def _mismatch_condition():
    valid_tier = or_(
        and_(
            McpServerRegistry.verdict.in_(TRUSTED_VERDICTS),
            McpServerRegistry.risk_tier.in_(("trusted", "general")),
        ),
        and_(
            McpServerRegistry.verdict == "ENTERPRISE_CONTROLLED",
            McpServerRegistry.risk_tier == "controlled",
        ),
        and_(
            McpServerRegistry.verdict == "CAUTION_LIMITED",
            McpServerRegistry.risk_tier == "caution",
        ),
        and_(
            McpServerRegistry.verdict.in_(("HIGH_RISK_ISOLATED", "KNOWN_THREAT")),
            McpServerRegistry.risk_tier == "high",
        ),
        and_(
            McpServerRegistry.verdict == "INSUFFICIENT",
            McpServerRegistry.risk_tier == "insufficient",
        ),
    )
    return and_(
        McpServerRegistry.verdict.in_(KNOWN_VERDICTS),
        or_(
            McpServerRegistry.risk_tier.is_(None),
            not_(valid_tier),
        ),
    )


def get_verdict_tier_mismatches(
    session: Session,
    limit: int = 50,
    include_counts: bool = True,
) -> VerdictTierMismatchResponse:
    limit = min(max(int(limit), 1), 200)
    mismatch = _mismatch_condition()

    total = session.query(func.count()).select_from(McpServerRegistry).filter(mismatch).scalar()
    total = int(total or 0)

    counts: Dict[str, int] = {}
    if include_counts:
        count_rows = (
            session.query(
                McpServerRegistry.server_id,
                func.count().label("mismatch_count"),
            )
            .filter(mismatch)
            .group_by(McpServerRegistry.server_id)
            .order_by(McpServerRegistry.server_id)
            .all()
        )
        counts = {str(server_id): int(count) for server_id, count in count_rows}

    records = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.confidence,
            McpServerRegistry.last_assessed,
        )
        .filter(mismatch)
        .order_by(
            McpServerRegistry.trust_score.asc().nulls_last(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(limit)
        .all()
    )

    rows = [
        VerdictTierMismatchRow(
            server_id=str(server_id),
            name=name,
            verdict=verdict,
            risk_tier=risk_tier,
            trust_score=trust_score,
            confidence=confidence,
            last_assessed=last_assessed,
        )
        for server_id, name, verdict, risk_tier, trust_score, confidence, last_assessed in records
    ]

    return VerdictTierMismatchResponse(
        total=total,
        counts=counts,
        rows=rows,
        generated_at=datetime.now(timezone.utc),
    )


@router.get(
    "/consistency/verdict-tier-mismatch",
    response_model=VerdictTierMismatchResponse,
)
def verdict_tier_mismatch_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    include_counts: bool = Query(default=True),
    session: Session = Depends(get_session),
) -> VerdictTierMismatchResponse:
    return get_verdict_tier_mismatches(
        session=session,
        limit=limit,
        include_counts=include_counts,
    )


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
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, risk_tier, trust_score, confidence, last_assessed)
                VALUES
                    ('consistent-server', 'Consistent', 'TRUSTED_GENERAL', 'trusted', 0.95, 0.99, '2025-01-01 00:00:00'),
                    ('mismatch-server', 'Mismatch', 'TRUSTED_GENERAL', 'high', 0.25, 0.80, '2025-01-02 00:00:00'),
                    ('insufficient-server', 'Insufficient', 'INSUFFICIENT', 'insufficient', 0.50, 0.60, '2025-01-03 00:00:00'),
                    ('high-server', 'High', 'HIGH_RISK_ISOLATED', 'high', 0.10, 0.90, '2025-01-04 00:00:00')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/consistency/verdict-tier-mismatch")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["rows"][0]["server_id"] == "mismatch-server"
        assert all(row["server_id"] != "consistent-server" for row in payload["rows"])

    print("PASS")