from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring"])


class ServerScoreEntry(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str]
    scored_at: datetime
    axes_count: int
    has_critical: bool


class ServerScoreHistoryResponse(BaseModel):
    servers: list[ServerScoreEntry]


def get_scoring_history(
    session: Session,
    hours: int = 24,
    limit: int = 50,
) -> list[ServerScoreEntry]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    query = text("""
        SELECT
            sr.server_id,
            sr.name,
            sr.risk_tier,
            sub.scored_at,
            sub.axes_count,
            sub.has_critical
        FROM McpServerRegistry sr
        INNER JOIN (
            SELECT
                server_id,
                scored_at,
                COUNT(*) as axes_count,
                MAX(CASE WHEN p_critical > 0.5 THEN 1 ELSE 0 END) as has_critical
            FROM McpLlmAxisScore
            WHERE scored_at >= :cutoff
            GROUP BY server_id, scored_at
        ) sub ON sr.server_id = sub.server_id
        ORDER BY sub.scored_at DESC
        LIMIT :limit
    """)

    params = {"cutoff": cutoff, "limit": limit}
    result = session.execute(query, params)
    rows = result.fetchall()

    return [
        ServerScoreEntry(
            server_id=row[0],
            name=row[1],
            risk_tier=row[2],
            scored_at=row[3],
            axes_count=row[4],
            has_critical=bool(row[5]),
        )
        for row in rows
    ]


@router.get("/scoring/history", response_model=ServerScoreHistoryResponse)
def get_history(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> ServerScoreHistoryResponse:
    servers = get_scoring_history(session, hours=hours, limit=limit)
    return ServerScoreHistoryResponse(servers=servers)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT,
                label_index INTEGER,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                scored_at TEXT NOT NULL,
                escalated INTEGER,
                escalated_to TEXT,
                FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id)
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    seed_servers = [
        ("srv-001", "Server Alpha", "low"),
        ("srv-002", "Server Beta", "medium"),
        ("srv-003", "Server Gamma", "high"),
    ]
    seed_scores = [
        ("srv-001", now - timedelta(minutes=30), "auth", 0.1),
        ("srv-001", now - timedelta(minutes=30), "safety", 0.2),
        ("srv-002", now - timedelta(hours=1), "auth", 0.6),
        ("srv-003", now - timedelta(hours=1, minutes=45), "auth", 0.3),
        ("srv-003", now - timedelta(hours=1, minutes=45), "safety", 0.8),
    ]

    with SessionLocal() as session:
        for srv_id, name, risk_tier in seed_servers:
            session.execute(
                text("INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES (:srv_id, :name, :risk_tier)"),
                {"srv_id": srv_id, "name": name, "risk_tier": risk_tier},
            )
        for server_id, scored_at, axis_name, p_critical in seed_scores:
            session.execute(
                text("""
                    INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, p_critical)
                    VALUES (:server_id, :scored_at, :axis_name, :p_critical)
                """),
                {"server_id": server_id, "scored_at": scored_at.isoformat(), "axis_name": axis_name, "p_critical": p_critical},
            )
        session.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)
    response = client.get("/api/scoring/history?hours=2&limit=50")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    servers = data.get("servers", [])
    assert len(servers) == 3, f"Expected 3 servers, got {len(servers)}"
    assert any(s["axes_count"] > 0 for s in servers), "Expected at least one server with axes_count > 0"
    print("PASS")