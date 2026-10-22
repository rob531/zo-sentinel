from datetime import datetime
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, McpServerRegistry


class RiskMixServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: Optional[str] = None
    trust_score: Optional[float] = None
    last_assessed: Optional[datetime] = None


class OrgServerRiskMixResponse(BaseModel):
    org_id: str
    total_servers: int
    average_trust_score: float
    tier_counts: Dict[str, int]
    servers: List[RiskMixServer]
    membership_source: Literal["registry", "api_key_scoped"]


RISK_TIERS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def get_org_risk_mix(
    org_id: str, session: Session = Depends(get_session)
) -> OrgServerRiskMixResponse:
    api_key_membership = exists().where(ApiKey.org_id == org_id)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_assessed,
        )
        .where(api_key_membership)
        .order_by(McpServerRegistry.server_id)
    ).all()

    tier_counts = {tier: 0 for tier in RISK_TIERS}
    servers: List[RiskMixServer] = []
    trust_scores: List[float] = []

    for row in rows:
        raw_tier = row.risk_tier.strip().upper() if row.risk_tier else "UNKNOWN"
        risk_tier = raw_tier if raw_tier in tier_counts else "UNKNOWN"
        tier_counts[risk_tier] += 1

        trust_score = float(row.trust_score) if row.trust_score is not None else None
        if trust_score is not None:
            trust_scores.append(trust_score)

        servers.append(
            RiskMixServer(
                server_id=row.server_id,
                name=row.name,
                risk_tier=risk_tier,
                verdict=row.verdict,
                trust_score=trust_score,
                last_assessed=row.last_assessed,
            )
        )

    average_trust_score = (
        round(sum(trust_scores) / len(trust_scores), 2) if trust_scores else 0.0
    )

    return OrgServerRiskMixResponse(
        org_id=org_id,
        total_servers=len(servers),
        average_trust_score=average_trust_score,
        tier_counts=tier_counts,
        servers=servers,
        membership_source="api_key_scoped",
    )


router = APIRouter()


@router.get(
    "/orgs/{org_id}/risk-mix",
    response_model=OrgServerRiskMixResponse,
)
def org_risk_mix(
    org_id: str, session: Session = Depends(get_session)
) -> OrgServerRiskMixResponse:
    return get_org_risk_mix(org_id=org_id, session=session)


app = FastAPI(title="Organization Server Risk Mix API")
app.include_router(router, prefix="/api")


def run() -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    registry_source VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    trust_score REAL,
                    last_assessed DATETIME,
                    last_seen DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id VARCHAR PRIMARY KEY,
                    key_hash VARCHAR,
                    label VARCHAR,
                    org_id VARCHAR,
                    created_at DATETIME
                )
                """
            )
        )

    now = datetime.utcnow()
    with testing_session() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, registry_source, risk_tier, verdict,
                    trust_score, last_assessed, last_seen
                ) VALUES (
                    :server_id, :name, :registry_source, :risk_tier, :verdict,
                    :trust_score, :last_assessed, :last_seen
                )
                """
            ),
            [
                {
                    "server_id": "critical-server",
                    "name": "Critical Server",
                    "registry_source": "test",
                    "risk_tier": "CRITICAL",
                    "verdict": "unsafe",
                    "trust_score": 0.2,
                    "last_assessed": now,
                    "last_seen": now,
                },
                {
                    "server_id": "high-server",
                    "name": "High Server",
                    "registry_source": "test",
                    "risk_tier": "HIGH",
                    "verdict": "caution",
                    "trust_score": 0.6,
                    "last_assessed": now,
                    "last_seen": now,
                },
                {
                    "server_id": "low-server",
                    "name": "Low Server",
                    "registry_source": "test",
                    "risk_tier": "LOW",
                    "verdict": "trusted",
                    "trust_score": 0.9,
                    "last_assessed": now,
                    "last_seen": now,
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO api_keys (id, key_hash, label, org_id, created_at)
                VALUES (:id, :key_hash, :label, :org_id, :created_at)
                """
            ),
            {
                "id": "key-1",
                "key_hash": "key-hash-1",
                "label": "Organization key",
                "org_id": "org-1",
                "created_at": now,
            },
        )
        session.commit()

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/org-1/risk-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["org_id"] == "org-1"
        assert payload["total_servers"] == 3
        assert payload["tier_counts"]["CRITICAL"] == 1
        assert payload["tier_counts"]["LOW"] == 1
        assert payload["membership_source"] == "api_key_scoped"

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())