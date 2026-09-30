from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api")


class ServerGap(BaseModel):
    server_id: str
    name: str
    last_scored_at: Optional[datetime]
    days_since_score: Optional[int]
    risk_tier: Optional[str]


class CoverageGapReport(BaseModel):
    total_servers: int
    gap_count: int
    gap_rate_pct: float
    servers: list[ServerGap]


def compute_coverage_gap(session: Session, threshold_days: int = 7) -> CoverageGapReport:
    query = text("""
        SELECT
            r.server_id,
            r.name,
            r.risk_tier,
            latest_score.scored_at AS last_scored_at,
            CASE
                WHEN latest_score.scored_at IS NULL THEN NULL
                ELSE julianday('now') - julianday(latest_score.scored_at)
            END AS days_since_score
        FROM mcp_server_registry r
        LEFT JOIN (
            SELECT server_id, MAX(scored_at) AS scored_at
            FROM mcp_llm_axis_scores
            GROUP BY server_id
        ) latest_score ON r.server_id = latest_score.server_id
        ORDER BY days_since_score DESC NULLS LAST
    """)

    rows = session.execute(query).fetchall()
    servers = []
    gap_count = 0

    for row in rows:
        server_id, name, risk_tier, last_scored_at, days_raw = row
        if not server_id or not name:
            continue

        days_since_score = int(days_raw) if days_raw is not None else None
        is_gap = days_since_score is None or days_since_score >= threshold_days

        servers.append(ServerGap(
            server_id=server_id,
            name=name,
            last_scored_at=last_scored_at,
            days_since_score=days_since_score,
            risk_tier=risk_tier,
        ))

        if is_gap:
            gap_count += 1

    total_servers = len(servers)
    gap_rate_pct = (gap_count / total_servers * 100) if total_servers > 0 else 0.0

    return CoverageGapReport(
        total_servers=total_servers,
        gap_count=gap_count,
        gap_rate_pct=gap_rate_pct,
        servers=servers,
    )


@router.get("/scoring/coverage-gap", response_model=CoverageGapReport)
def get_coverage_gap(
    threshold_days: int = 7,
    session: Session = Depends(get_session),
) -> CoverageGapReport:
    return compute_coverage_gap(session, threshold_days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                confidence REAL,
                description TEXT,
                first_seen TIMESTAMP,
                last_assessed TIMESTAMP,
                last_scanned TIMESTAMP,
                last_seen TIMESTAMP,
                meta TEXT,
                registry_source TEXT,
                scan_count INTEGER,
                trust_score REAL,
                url TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                adapter_sha256 TEXT,
                axis_name TEXT,
                decision_rule_version TEXT,
                escalated BOOLEAN,
                escalated_to TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                scored_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                dispute_reason TEXT,
                created_at TIMESTAMP,
                resolved_at TIMESTAMP,
                resolution TEXT,
                status TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS orgs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT UNIQUE
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT UNIQUE,
                org_id INTEGER
            )
        """))

    test_session = TestingSession()

    now = datetime.now(timezone.utc)
    eight_days_ago = now - timedelta(days=8)

    all_servers = [
        {"server_id": "srv-001", "name": "Server A", "risk_tier": "low"},
        {"server_id": "srv-002", "name": "Server B", "risk_tier": "medium"},
        {"server_id": "srv-003", "name": "Server C", "risk_tier": "high"},
        {"server_id": "srv-004", "name": "Server D", "risk_tier": "critical"},
        {"server_id": "srv-005", "name": "Server E", "risk_tier": "low"},
    ]
    for s in all_servers:
        test_session.execute(
            text("INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (:server_id, :name, :risk_tier)"),
            s,
        )

    scored = [
        {"server_id": "srv-001", "scored_at": now},
        {"server_id": "srv-002", "scored_at": now},
    ]
    for s in scored:
        test_session.execute(
            text("INSERT INTO mcp_llm_axis_scores (server_id, scored_at) VALUES (:server_id, :scored_at)"),
            s,
        )

    test_session.execute(text("INSERT INTO orgs (id, name, slug) VALUES (1, 'Test Org', 'test-org')"))
    test_session.execute(text("INSERT INTO users (id, username, email, org_id) VALUES (1, 'testuser', 'test@example.com', 1)"))
    test_session.commit()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/api/scoring/coverage-gap?threshold_days=7")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["gap_count"] == 3, f"Expected gap_count=3, got {data['gap_count']}"
    assert data["gap_rate_pct"] == 60.0, f"Expected gap_rate_pct=60.0, got {data['gap_rate_pct']}"

    print(f"total_servers={data['total_servers']}, gap_count={data['gap_count']}, gap_rate_pct={data['gap_rate_pct']}")
    print("PASS")