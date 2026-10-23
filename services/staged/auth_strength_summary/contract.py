from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class ServerScore(BaseModel):
    server_id: int
    name: str
    p_top: float
    p_critical: float
    label: str | None


class DistributionEntry(BaseModel):
    label: str | None
    label_index: int | None
    count: int
    avg_p_critical: float


class Summary(BaseModel):
    total_servers: int
    high_risk_count: int
    distribution: list[DistributionEntry]


class AuthStrengthSummaryResponse(BaseModel):
    summary: Summary
    servers: list[ServerScore]


def get_auth_strength_data(session: Session) -> AuthStrengthSummaryResponse:
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == "auth_strength")
    )
    rows = session.execute(stmt).all()

    servers = [
        ServerScore(
            server_id=r.server_id,
            name=r.name,
            p_top=r.p_top,
            p_critical=r.p_critical,
            label=r.label,
        )
        for r in rows
    ]

    total_servers = len(servers)
    high_risk_count = sum(1 for s in servers if s.p_critical >= 0.5)

    dist_map: dict[tuple[str | None, int | None], list[float]] = {}
    for s in servers:
        key = (s.label, s.label_index if hasattr(s, "label_index") else None)
        if key not in dist_map:
            dist_map[key] = []
        dist_map[key].append(s.p_critical)

    distribution = [
        DistributionEntry(
            label=k[0],
            label_index=k[1],
            count=len(vals),
            avg_p_critical=sum(vals) / len(vals) if vals else 0.0,
        )
        for k, vals in dist_map.items()
    ]

    summary = Summary(
        total_servers=total_servers,
        high_risk_count=high_risk_count,
        distribution=distribution,
    )

    return AuthStrengthSummaryResponse(summary=summary, servers=servers)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    app = FastAPI(lifespan=lifespan)

    @app.post("/api/scoring/auth_strength/summary", response_model=AuthStrengthSummaryResponse)
    def auth_strength_summary(session: Session = Depends(get_session)) -> AuthStrengthSummaryResponse:
        return get_auth_strength_data(session)

    return app


def _run_self_test() -> int:
    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with in_memory_engine.begin() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id INTEGER PRIMARY KEY, name TEXT, url TEXT, registry_source TEXT, description TEXT, first_seen TEXT, last_seen TEXT, last_scanned TEXT, last_assessed TEXT, meta TEXT, verdict TEXT, verdict_reasoning TEXT, risk_tier TEXT, trust_score REAL, confidence REAL, scan_count INTEGER)"))
        conn.execute(text("CREATE TABLE mcp_llm_axis_scores (id INTEGER PRIMARY KEY, server_id INTEGER, axis_name TEXT, model_version TEXT, decision_rule_version TEXT, p_top REAL, p_critical REAL, p_danger REAL, probs TEXT, label TEXT, label_index INTEGER, escalated INTEGER, escalated_to TEXT, scored_at TEXT, adapter_sha256 TEXT)"))
        conn.execute(text("INSERT INTO mcp_server_registry (server_id, name, url) VALUES (1, 'server_a', 'http://a.local'), (2, 'server_b', 'http://b.local'), (3, 'server_c', 'http://c.local'), (4, 'server_d', 'http://d.local'), (5, 'server_e', 'http://e.local')"))
        conn.execute(text("INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, label, label_index) VALUES (1, 'auth_strength', 0.9, 0.6, 'STRONG', 1), (2, 'auth_strength', 0.7, 0.4, 'MODERATE', 2), (3, 'auth_strength', 0.8, 0.7, 'STRONG', 1), (4, 'auth_strength', 0.5, 0.3, 'WEAK', 3), (5, 'auth_strength', 0.6, 0.55, 'MODERATE', 2)"))

    TestingSessionLocal = sessionmaker(bind=in_memory_engine, expire_on_commit=False)
    that_app = create_app()

    def _override_get_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(that_app)
    resp = client.post("/api/scoring/auth_strength/summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["summary"]["total_servers"] == 5, f"Expected 5 servers, got {data['summary']['total_servers']}"
    assert len(data["summary"]["distribution"]) >= 2, f"Expected >=2 distribution entries, got {len(data['summary']['distribution'])}"
    print("PASS")
    return 0


if __name__ == "__main__":
    from typing import Generator
    raise SystemExit(_run_self_test())