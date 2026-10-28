from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_tier_impact
except ImportError:
    from services.staged.threat_assoc_tier_impact_api.logic import get_tier_impact

router = APIRouter(prefix="/api", tags=["threat"])


@router.get("/threat/associations/tier-impact")
def tier_impact(days: int = 30, session: Session = Depends(get_session)):
    return get_tier_impact(session, days)


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import (
        Column,
        DateTime,
        Float,
        Integer,
        MetaData,
        String,
        Table,
        create_engine,
        insert,
    )
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    associations = Table(
        "mcp_threat_associations",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("threat_type", String),
        Column("source", String),
        Column("severity", String),
        Column("confidence", Float),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    registry = Table(
        "mcp_server_registry",
        metadata,
        Column("server_id", String, primary_key=True),
        Column("name", String),
        Column("risk_tier", String),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            insert(registry),
            [
                {"server_id": "srv-alpha", "name": "Alpha", "risk_tier": "high"},
                {"server_id": "srv-beta", "name": "Beta", "risk_tier": "high"},
                {"server_id": "srv-gamma", "name": "Gamma", "risk_tier": "medium"},
            ],
        )
        connection.execute(
            insert(associations),
            [
                {
                    "server_id": "srv-alpha",
                    "threat_type": "malware",
                    "source": "intel",
                    "severity": "high",
                    "confidence": 0.8,
                    "created_at": now - timedelta(days=1),
                },
                {
                    "server_id": "srv-alpha",
                    "threat_type": "phishing",
                    "source": "intel",
                    "severity": "medium",
                    "confidence": 0.6,
                    "created_at": now - timedelta(days=2),
                },
                {
                    "server_id": "srv-beta",
                    "threat_type": "exploit",
                    "source": "scanner",
                    "severity": "critical",
                    "confidence": 0.7,
                    "created_at": now - timedelta(days=3),
                },
                {
                    "server_id": "srv-gamma",
                    "threat_type": "malware",
                    "source": "intel",
                    "severity": "high",
                    "confidence": 0.9,
                    "created_at": now - timedelta(days=4),
                },
            ],
        )

    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with test_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat/associations/tier-impact", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_associations"] == 4, payload
        assert payload["distinct_servers"] == 3, payload
        tiers = {tier["risk_tier"]: tier for tier in payload["tiers"]}
        assert tiers["high"]["association_rate"] == 1.0, payload
        assert tiers["medium"]["association_rate"] == 1.0, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()