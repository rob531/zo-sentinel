from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    probs: Optional[dict]
    escalated: bool


class SummaryBlock(BaseModel):
    overall_risk_p_top: Optional[float]
    composite_verdict: str
    risk_tier: str
    decision_rule_version: Optional[str]
    model_version: Optional[str]
    scored_at: Optional[datetime]


class ScorecardResponse(BaseModel):
    server_id: str
    name: str
    trust_score: Optional[float]
    verdict: str
    confidence: Optional[float]
    risk_tier: str
    axes: list[AxisScore]
    summary: SummaryBlock


def get_scorecard_data(server_id: str, db: Session) -> dict:
    server_query = text("""
        SELECT name, trust_score, verdict, risk_tier, confidence
        FROM mcp_server_registry
        WHERE server_id = :server_id
    """)
    server_result = db.execute(server_query, {"server_id": server_id}).fetchone()
    if not server_result:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axes_query = text("""
        SELECT axis_name, label, p_top, p_critical, p_danger, probs, escalated
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        ORDER BY label_index
    """)
    axes_result = db.execute(axes_query, {"server_id": server_id}).fetchall()

    if not axes_result:
        raise HTTPException(status_code=404, detail=f"No scorecard data for server {server_id}")

    p_top_values = [row.p_top for row in axes_result if row.p_top is not None]
    overall_risk_p_top = max(p_top_values) if p_top_values else None

    summary_query = text("""
        SELECT decision_rule_version, model_version, scored_at
        FROM mcp_llm_axis_scores
        WHERE server_id = :server_id
        LIMIT 1
    """)
    summary_result = db.execute(summary_query, {"server_id": server_id}).fetchone()

    axes = [
        AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            probs=row.probs if isinstance(row.probs, dict) else None,
            escalated=bool(row.escalated)
        )
        for row in axes_result
    ]

    summary = SummaryBlock(
        overall_risk_p_top=overall_risk_p_top,
        composite_verdict=server_result.verdict or "unknown",
        risk_tier=server_result.risk_tier or "unknown",
        decision_rule_version=summary_result.decision_rule_version if summary_result else None,
        model_version=summary_result.model_version if summary_result else None,
        scored_at=summary_result.scored_at if summary_result else None
    )

    return ScorecardResponse(
        server_id=server_id,
        name=server_result.name or "unknown",
        trust_score=server_result.trust_score,
        verdict=server_result.verdict or "unknown",
        confidence=server_result.confidence,
        risk_tier=server_result.risk_tier or "unknown",
        axes=axes,
        summary=summary
    ).model_dump()


@router.get("/api/servers/{server_id}/scorecard", response_model=ScorecardResponse)
def get_server_scorecard(server_id: str, db: Session = Depends(get_session)) -> dict:
    return get_scorecard_data(server_id, db)


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SEED_SERVERS = [
        {"server_id": "srv-001", "name": "Server Alpha", "trust_score": 0.85, "verdict": "approved", "risk_tier": "low", "confidence": 0.92},
        {"server_id": "srv-002", "name": "Server Beta", "trust_score": 0.45, "verdict": "flagged", "risk_tier": "medium", "confidence": 0.78},
        {"server_id": "srv-003", "name": "Server Gamma", "trust_score": 0.12, "verdict": "rejected", "risk_tier": "high", "confidence": 0.95},
    ]

    AXIS_NAMES = ["security", "reliability", "compliance", "performance", "scalability", "maintainability", "observability"]
    LABELS = ["critical", "high", "medium", "low"]

    def create_tables(engine):
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    risk_tier TEXT,
                    confidence REAL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    probs TEXT,
                    escalated INTEGER,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    scored_at TEXT,
                    FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
                )
            """))
            conn.commit()

    def seed_data(session):
        for srv in SEED_SERVERS:
            session.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, trust_score, verdict, risk_tier, confidence)
                VALUES (:server_id, :name, :trust_score, :verdict, :risk_tier, :confidence)
            """), srv)

        for srv in SEED_SERVERS:
            for idx, axis in enumerate(AXIS_NAMES):
                label = LABELS[idx % len(LABELS)]
                escalated = 1 if label == "critical" else 0
                p_top_val = 0.9 - (idx * 0.1)
                session.execute(text("""
                    INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, probs, escalated, decision_rule_version, model_version, scored_at)
                    VALUES (:server_id, :axis_name, :label, :label_index, :p_top, :p_critical, :p_danger, :probs, :escalated, 'v1.0', 'gpt-4', '2024-01-15T10:00:00')
                """), {
                    "server_id": srv["server_id"],
                    "axis_name": axis,
                    "label": label,
                    "label_index": idx,
                    "p_top": p_top_val,
                    "p_critical": p_top_val * 0.7,
                    "p_danger": p_top_val * 0.4,
                    "probs": json.dumps({"critical": 0.3, "high": 0.4, "medium": 0.2, "low": 0.1}),
                    "escalated": escalated
                })
        session.commit()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    create_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    seed_data(session)
    session.close()

    def override_get_session():
        yield session

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    from fastapi.testclient import TestClient
    client = TestClient(app)

    all_passed = True
    for srv in SEED_SERVERS:
        resp = client.get(f"/api/servers/{srv['server_id']}/scorecard")
        if resp.status_code != 200:
            print(f"FAIL: {srv['server_id']} got {resp.status_code}")
            all_passed = False
            continue
        data = resp.json()
        if len(data["axes"]) != 7:
            print(f"FAIL: {srv['server_id']} axes length {len(data['axes'])} != 7")
            all_passed = False
            continue
        if not data["risk_tier"]:
            print(f"FAIL: {srv['server_id']} risk_tier is empty")
            all_passed = False
            continue

    if all_passed:
        print("PASS")
    else:
        print("FAIL")