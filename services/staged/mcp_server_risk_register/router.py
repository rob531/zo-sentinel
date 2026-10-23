from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Any

from app.db import get_session
from sqlalchemy.orm import Session
from sqlalchemy import text

router = APIRouter()


class Entry(BaseModel):
    id: int
    verdict: str
    risk_tier: str
    criteria_version: str
    assessed_at: datetime


class RiskRegisterResponse(BaseModel):
    server_id: int
    entries: list[Entry]


def get_risk_register_logic(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT id, verdict, risk_tier, criteria_version, assessed_at
            FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    return {
        "server_id": server_id,
        "entries": [
            {
                "id": row.id,
                "verdict": row.verdict,
                "risk_tier": row.risk_tier,
                "criteria_version": row.criteria_version,
                "assessed_at": row.assessed_at,
            }
            for row in rows
        ],
    }


@router.get("/servers/{server_id}/risk-register", response_model=RiskRegisterResponse)
def get_risk_register(server_id: int, session: Session = Depends(get_session)):
    return get_risk_register_logic(session, server_id)


def get_freshness(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return {"freshness_score": 0.0}
    age = (datetime.now() - row.assessed_at).total_seconds()
    freshness_score = max(0.0, 1.0 - (age / 86400.0))
    return {"freshness_score": freshness_score}


def get_momentum(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 2
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    if len(rows) < 2:
        return {"momentum": 0.0}
    return {"momentum": 1.0 if rows[0].verdict != rows[1].verdict else 0.0}


def get_server_staleness(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return {"staleness_score": 1.0}
    age = (datetime.now() - row.assessed_at).total_seconds()
    return {"staleness_score": min(1.0, age / 86400.0)}


def get_score_summary(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, risk_tier FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return {"summary": {"verdict": None, "risk_tier": None}}
    return {"summary": {"verdict": row.verdict, "risk_tier": row.risk_tier}}


def get_scoring_statistics(session: Session) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT risk_tier, COUNT(*) as cnt FROM mcp_risk_register GROUP BY risk_tier
    """))
    rows = result.fetchall()
    return {"statistics": {row.risk_tier: row.cnt for row in rows}}


def get_family_risk_summary(session: Session, family_id: int) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT risk_tier, COUNT(*) as cnt FROM mcp_risk_register
        JOIN mcp_server_registry ON mcp_risk_register.server_id = mcp_server_registry.id
        WHERE mcp_server_registry.family_id = :family_id
        GROUP BY risk_tier
    """), {"family_id": family_id})
    rows = result.fetchall()
    return {"family_risk": {row.risk_tier: row.cnt for row in rows}}


def get_perspective_data(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, risk_tier, criteria_version, assessed_at
            FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 10
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    return {
        "perspective": [
            {"verdict": r.verdict, "risk_tier": r.risk_tier, "criteria_version": r.criteria_version, "assessed_at": r.assessed_at}
            for r in rows
        ]
    }


def get_servers_report(session: Session, limit: int = 100) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT mcp_risk_register.server_id, verdict, risk_tier, assessed_at
        FROM mcp_risk_register
        JOIN mcp_server_registry ON mcp_risk_register.server_id = mcp_server_registry.id
        ORDER BY assessed_at DESC LIMIT :limit
    """), {"limit": limit})
    rows = result.fetchall()
    return {
        "report": [
            {"server_id": r.server_id, "verdict": r.verdict, "risk_tier": r.risk_tier, "assessed_at": r.assessed_at}
            for r in rows
        ]
    }


def get_top_risk_servers(session: Session, limit: int = 10) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT server_id, risk_tier, assessed_at FROM mcp_risk_register
        WHERE risk_tier IN ('critical', 'high')
        ORDER BY assessed_at DESC LIMIT :limit
    """), {"limit": limit})
    rows = result.fetchall()
    return {"top_risk_servers": [{"server_id": r.server_id, "risk_tier": r.risk_tier} for r in rows]}


def get_risk_summary(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, risk_tier FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return {"verdict": None, "risk_tier": None}
    return {"verdict": row.verdict, "risk_tier": row.risk_tier}


def get_tier_changes(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT risk_tier, assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 2
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    if len(rows) < 2:
        return {"tier_changed": False}
    return {"tier_changed": rows[0].risk_tier != rows[1].risk_tier}


def get_risk_tier_snapshot(session: Session) -> dict[str, Any]:
    result = session.execute(text("""
        SELECT server_id, risk_tier FROM mcp_risk_register
        WHERE assessed_at = (SELECT MAX(assessed_at) FROM mcp_risk_register mr2 WHERE mr2.server_id = mcp_risk_register.server_id)
    """))
    rows = result.fetchall()
    return {"snapshot": [{"server_id": r.server_id, "risk_tier": r.risk_tier} for r in rows]}


def seed_server_registry(session: Session, server_id: int, data: dict[str, Any]) -> None:
    session.execute(
        text("""
            INSERT OR REPLACE INTO mcp_server_registry (id, name, family_id)
            VALUES (:server_id, :name, :family_id)
        """),
        {"server_id": server_id, "name": data.get("name", ""), "family_id": data.get("family_id", 1)}
    )
    session.commit()


def get_risk_tier_trend(session: Session, server_id: int, days: int = 30) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT risk_tier, COUNT(*) as cnt
            FROM mcp_risk_register
            WHERE server_id = :server_id AND assessed_at >= NOW() - INTERVAL '1 day' * :days
            GROUP BY risk_tier
        """),
        {"server_id": server_id, "days": days}
    )
    rows = result.fetchall()
    return {"trend": {row.risk_tier: row.cnt for row in rows}}


def update_trajectories(session: Session, server_ids: list[int]) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT server_id, risk_tier, assessed_at FROM mcp_risk_register
            WHERE server_id = ANY(:server_ids)
            ORDER BY assessed_at DESC
        """),
        {"server_ids": server_ids}
    )
    rows = result.fetchall()
    return {"updated": len(rows)}


def mesh_memory_endpoint(session: Session, key: str) -> dict[str, Any]:
    result = session.execute(text("SELECT content FROM mesh_memory WHERE key = :key"), {"key": key})
    row = result.fetchone()
    return {"content": row.content if row else None}


def delete_dispute(session: Session, dispute_id: int) -> dict[str, Any]:
    session.execute(text("DELETE FROM mcp_score_disputes WHERE id = :id"), {"id": dispute_id})
    session.commit()
    return {"deleted": dispute_id}


def get_summary(session: Session, server_id: int) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, risk_tier, assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY assessed_at DESC LIMIT 1
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return {"summary": None}
    return {"summary": {"verdict": row.verdict, "risk_tier": row.risk_tier, "assessed_at": row.assessed_at}}


def scoring_timeline(session: Session, server_id: int, days: int = 30) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT verdict, risk_tier, assessed_at FROM mcp_risk_register
            WHERE server_id = :server_id AND assessed_at >= NOW() - INTERVAL '1 day' * :days
            ORDER BY assessed_at ASC
        """),
        {"server_id": server_id, "days": days}
    )
    rows = result.fetchall()
    return {
        "timeline": [
            {"verdict": r.verdict, "risk_tier": r.risk_tier, "assessed_at": r.assessed_at}
            for r in rows
        ]
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    that_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_risk_register (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                verdict TEXT,
                risk_tier TEXT,
                criteria_version TEXT,
                assessed_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                name TEXT,
                family_id INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY,
                server_id INTEGER
            )
        """))
        conn.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as db:
        db.execute(
            text("""
                INSERT INTO mcp_risk_register (id, server_id, verdict, risk_tier, criteria_version, assessed_at)
                VALUES (1, 42, 'compliant', 'low', 'v1.2', '2024-01-15 10:30:00')
            """)
        )
        db.execute(
            text("""
                INSERT INTO mcp_risk_register (id, server_id, verdict, risk_tier, criteria_version, assessed_at)
                VALUES (2, 42, 'non_compliant', 'high', 'v1.2', '2024-01-20 14:00:00')
            """)
        )
        db.commit()

    client = TestClient(that_app)
    response = client.get("/servers/42/risk-register")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == 42
    assert len(data["entries"]) == 2
    assert data["entries"][0]["verdict"] == "non_compliant"
    assert data["entries"][0]["risk_tier"] == "high"
    assert data["entries"][1]["verdict"] == "compliant"
    assert data["entries"][1]["risk_tier"] == "low"
    print("PASS")