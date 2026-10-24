from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TierSummary(BaseModel):
    risk_tier: str
    count: int
    avg_trust_score: float
    avg_confidence: float
    stale_assessments: int
    latest_assessed: Optional[str]


class SourceSummary(BaseModel):
    registry_source: str
    tiers: List[TierSummary]
    total_servers: int


class RegistryVerdictChangeLedger(BaseModel):
    generated_at: str
    days: int
    stale_after_days: int
    sources: List[SourceSummary]
    total_servers: int


app = FastAPI(title="registry_verdict_change_ledger_api")


def _iso_timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _build_ledger(session: Session, days: int, registry_source: Optional[str]) -> RegistryVerdictChangeLedger:
    days = max(1, min(365, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = "SELECT server_id, registry_source, risk_tier, trust_score, confidence, last_assessed FROM mcp_server_registry"
    parameters = {}
    if registry_source is not None:
        statement += " WHERE registry_source = :registry_source"
        parameters["registry_source"] = registry_source
    statement += " ORDER BY registry_source, risk_tier, server_id"

    rows = session.execute(text(statement), parameters).mappings().all()
    grouped = {}
    for row in rows:
        source = str(row["registry_source"])
        tier = str(row["risk_tier"])
        grouped.setdefault(source, {}).setdefault(tier, []).append(row)

    sources = []
    for source in sorted(grouped):
        tiers = []
        source_total = 0
        for tier in sorted(grouped[source]):
            tier_rows = grouped[source][tier]
            source_total += len(tier_rows)

            trust_values = [float(row["trust_score"]) for row in tier_rows if row["trust_score"] is not None]
            confidence_values = [float(row["confidence"]) for row in tier_rows if row["confidence"] is not None]

            stale = 0
            assessed = []
            for row in tier_rows:
                value = row["last_assessed"]
                if value is None:
                    continue
                if isinstance(value, str):
                    try:
                        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                value = value.astimezone(timezone.utc)
                assessed.append(value)
                if value < cutoff:
                    stale += 1

            tiers.append(
                TierSummary(
                    risk_tier=tier,
                    count=len(tier_rows),
                    avg_trust_score=(sum(trust_values) / len(trust_values)) if trust_values else 0.0,
                    avg_confidence=(sum(confidence_values) / len(confidence_values)) if confidence_values else 0.0,
                    stale_assessments=stale,
                    latest_assessed=_iso_timestamp(max(assessed)) if assessed else None,
                )
            )

        sources.append(
            SourceSummary(
                registry_source=source,
                tiers=tiers,
                total_servers=source_total,
            )
        )

    return RegistryVerdictChangeLedger(
        generated_at=datetime.now(timezone.utc).isoformat(),
        days=days,
        stale_after_days=days,
        sources=sources,
        total_servers=sum(source.total_servers for source in sources),
    )


@app.get("/api/registry/verdict-changes", response_model=RegistryVerdictChangeLedger)
def registry_verdict_changes(
    days: int = Query(30),
    registry_source: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    return _build_ledger(session, days, registry_source)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    url TEXT,
                    description TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    confidence REAL,
                    last_assessed TIMESTAMP,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    scan_count INTEGER,
                    risk_tier TEXT,
                    meta TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "server-1",
            "name": "Trusted One",
            "registry_source": "source-a",
            "url": "https://one.invalid",
            "description": "one",
            "trust_score": 0.8,
            "verdict": "trusted",
            "verdict_reasoning": "seed",
            "confidence": 0.9,
            "last_assessed": now - timedelta(days=2),
            "first_seen": now - timedelta(days=100),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 2,
            "risk_tier": "trusted",
            "meta": "{}",
        },
        {
            "server_id": "server-2",
            "name": "Trusted Two",
            "registry_source": "source-a",
            "url": "https://two.invalid",
            "description": "two",
            "trust_score": 0.6,
            "verdict": "trusted",
            "verdict_reasoning": "seed",
            "confidence": 0.7,
            "last_assessed": now - timedelta(days=45),
            "first_seen": now - timedelta(days=100),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "trusted",
            "meta": "{}",
        },
        {
            "server_id": "server-3",
            "name": "Medium",
            "registry_source": "source-b",
            "url": "https://three.invalid",
            "description": "three",
            "trust_score": 0.5,
            "verdict": "review",
            "verdict_reasoning": "seed",
            "confidence": 0.6,
            "last_assessed": now - timedelta(days=5),
            "first_seen": now - timedelta(days=80),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "medium",
            "meta": "{}",
        },
        {
            "server_id": "server-4",
            "name": "High Risk",
            "registry_source": "source-b",
            "url": "https://four.invalid",
            "description": "four",
            "trust_score": 0.2,
            "verdict": "risky",
            "verdict_reasoning": "seed",
            "confidence": 0.4,
            "last_assessed": now - timedelta(days=1),
            "first_seen": now - timedelta(days=40),
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 3,
            "risk_tier": "high",
            "meta": "{}",
        },
    ]

    session = TestingSession()
    try:
        for row in rows:
            session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (
                        server_id, name, registry_source, url, description,
                        trust_score, verdict, verdict_reasoning, confidence,
                        last_assessed, first_seen, last_seen, last_scanned,
                        scan_count, risk_tier, meta
                    ) VALUES (
                        :server_id, :name, :registry_source, :url, :description,
                        :trust_score, :verdict, :verdict_reasoning, :confidence,
                        :last_assessed, :first_seen, :last_seen, :last_scanned,
                        :scan_count, :risk_tier, :meta
                    )
                    """
                ),
                row,
            )
        session.commit()

        def override_get_session():
            yield session

        app.dependency_overrides[get_session] = override_get_session
        try:
            response = TestClient(app).get("/api/registry/verdict-changes")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["sources"]) == 2, payload
            assert sum(source["total_servers"] for source in payload["sources"]) == 4, payload

            trusted = next(
                tier
                for source in payload["sources"]
                for tier in source["tiers"]
                if tier["risk_tier"] == "trusted"
            )
            assert trusted["avg_trust_score"] == 0.7, trusted
            assert trusted["stale_assessments"] == 1, trusted
        finally:
            app.dependency_overrides.pop(get_session, None)
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()