"""Family rollup API - aggregates servers by family/registry_source."""
from __future__ import annotations

import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# REAL data layer imports - NOT local models
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class TierCount(BaseModel):
    """Risk tier count."""
    tier: str
    count: int


class FamilyStats(BaseModel):
    """Per-family statistics."""
    source: str
    server_count: int
    avg_trust_score: float | None
    tiers: dict[str, int]


class FamiliesResponse(BaseModel):
    """Response with family list."""
    families: list[FamilyStats]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """No-op lifespan."""
    yield


# Application
the_app = FastAPI(lifespan=lifespan)


@the_app.get("/api/registry/families", response_model=FamiliesResponse)
def get_families(session: Session = Depends(get_session)) -> FamiliesResponse:
    """Get aggregated stats grouped by registry_source (family dimension)."""
    # Postgres-portable query: join registry to axis_scores, group by registry_source
    query = text("""
        SELECT
            r.registry_source AS source,
            COUNT(DISTINCT r.server_id) AS server_count,
            AVG(r.trust_score) AS avg_trust_score,
            r.risk_tier,
            COUNT(*) AS tier_count
        FROM McpServerRegistry r
        LEFT JOIN McpLlmAxisScore a ON r.server_id = a.server_id
        GROUP BY r.registry_source, r.risk_tier
        ORDER BY r.registry_source
    """)
    rows = session.execute(query).fetchall()

    families: dict[str, dict] = defaultdict(lambda: {"tiers": defaultdict(int)})
    for row in rows:
        source = row.source or "unknown"
        families[source]["source"] = source
        families[source]["server_count"] = row.server_count
        if row.avg_trust_score is not None:
            existing = families[source].get("avg_trust_score")
            if existing is None:
                families[source]["avg_trust_score"] = row.avg_trust_score
            else:
                families[source]["avg_trust_score"] = (existing + row.avg_trust_score) / 2
        families[source]["tiers"][row.risk_tier] += row.tier_count

    result = []
    for source, data in families.items():
        tiers = {tier: count for tier, count in data["tiers"].items()}
        result.append(FamilyStats(
            source=source,
            server_count=data["server_count"],
            avg_trust_score=data.get("avg_trust_score"),
            tiers=tiers,
        ))

    return FamiliesResponse(families=result)


def seed_db(session: Session) -> None:
    """Seed test data: 2 families, 3 servers each."""
    families = ["vendor_alpha", "vendor_beta"]
    tiers = ["low", "medium", "high"]

    for i, family in enumerate(families):
        for j in range(3):
            server_id = f"srv_{family}_{j}"
            session.execute(
                text("""
                    INSERT INTO McpServerRegistry
                    (server_id, name, url, registry_source, risk_tier, trust_score,
                     confidence, description, first_seen, last_seen, last_scanned,
                     last_assessed, scan_count, meta, verdict, verdict_reasoning)
                    VALUES
                    (:server_id, :name, :url, :registry_source, :risk_tier, :trust_score,
                     :confidence, :description, :first_seen, :last_seen, :last_scanned,
                     :last_assessed, :scan_count, :meta, :verdict, :verdict_reasoning)
                """),
                {
                    "server_id": server_id,
                    "name": f"Server {j} of {family}",
                    "url": f"http://{family}.example.com/{j}",
                    "registry_source": family,
                    "risk_tier": tiers[j % 3],
                    "trust_score": 0.5 + (i * 3 + j) * 0.1,
                    "confidence": 0.9,
                    "description": f"Test server {j} from {family}",
                    "first_seen": "2024-01-01",
                    "last_seen": "2024-01-15",
                    "last_scanned": "2024-01-15",
                    "last_assessed": "2024-01-15",
                    "scan_count": 5,
                    "meta": "{}",
                    "verdict": "approved",
                    "verdict_reasoning": "Test data",
                },
            )
            session.execute(
                text("""
                    INSERT INTO McpLlmAxisScore
                    (server_id, axis_name, label, label_index, model_version,
                     decision_rule_version, probs, p_top, p_critical, p_danger,
                     escalated, escalated_to, scored_at)
                    VALUES
                    (:server_id, :axis_name, :label, :label_index, :model_version,
                     :decision_rule_version, :probs, :p_top, :p_critical, :p_danger,
                     :escalated, :escalated_to, :scored_at)
                """),
                {
                    "server_id": server_id,
                    "axis_name": "safety",
                    "label": "safe",
                    "label_index": 0,
                    "model_version": "v1",
                    "decision_rule_version": "v1",
                    "probs": "[0.9, 0.1]",
                    "p_top": 0.9,
                    "p_critical": 0.05,
                    "p_danger": 0.05,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": "2024-01-15",
                },
            )
    session.commit()


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER DEFAULT 0,
                meta TEXT DEFAULT '{}',
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                probs TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER DEFAULT 0,
                escalated_to TEXT,
                scored_at TEXT
            )
        """))

    def override_get_session() -> Session:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    the_app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        seed_db(session)

    client = TestClient(the_app)
    response = client.get("/api/registry/families")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    families = data.get("families", [])
    assert len(families) >= 2, f"Expected >= 2 families, got {len(families)}"

    print("PASS")
    sys.exit(0)