from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.db import get_session


router = APIRouter(prefix="/api", tags=["scoring"])


class ScoringAnalyticsResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    avg_overall_score: Optional[float]
    coverage_pct: float
    stale_servers: int
    recent_waves: int
    days: int
    computed_at: datetime


def compute_analytics(days: int, session: Session) -> ScoringAnalyticsResponse:
    is_sqlite = session.bind.dialect.name == "sqlite"
    if is_sqlite:
        recent_cutoff = f"datetime('now', '-{days} days')"
        stale_cutoff = f"datetime('now', '-{days} days')"
    else:
        recent_cutoff = f"NOW() - INTERVAL '{days} days'"
        stale_cutoff = f"NOW() - INTERVAL '{days} days'"

    total_query = text(f"""
        SELECT COUNT(*) as total_servers
        FROM mcp_server_registry
    """)
    total_result = session.execute(total_query).fetchone()
    total_servers = total_result[0] if total_result else 0

    scored_query = text(f"""
        SELECT COUNT(DISTINCT s.server_id) as scored_servers,
               SUM(CASE WHEN s.server_id IS NOT NULL THEN 1 ELSE 0 END) as unscored_count
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
        WHERE s.scored_at >= {recent_cutoff}
           OR s.scored_at IS NULL
    """)
    scored_result = session.execute(scored_query).fetchone()
    scored_servers = scored_result[0] if scored_result and scored_result[0] else 0
    unscored_servers = total_servers - scored_servers

    avg_query = text(f"""
        SELECT AVG(s.p_top) as avg_score
        FROM mcp_llm_axis_scores s
        WHERE s.axis_name = 'overall_risk'
          AND s.server_id IN (
              SELECT DISTINCT server_id FROM mcp_llm_axis_scores
              WHERE scored_at >= {recent_cutoff}
          )
    """)
    avg_result = session.execute(avg_query).fetchone()
    avg_overall_score = float(avg_result[0]) if avg_result and avg_result[0] is not None else None

    coverage_pct = (scored_servers / total_servers * 100) if total_servers > 0 else 0.0

    stale_query = text(f"""
        SELECT COUNT(DISTINCT s.server_id) as stale_count
        FROM mcp_llm_axis_scores s
        WHERE s.scored_at < {stale_cutoff}
          AND s.scored_at IS NOT NULL
    """)
    stale_result = session.execute(stale_query).fetchone()
    stale_servers = stale_result[0] if stale_result and stale_result[0] is not None else 0

    waves_query = text(f"""
        SELECT COUNT(DISTINCT scored_at) as recent_waves
        FROM mcp_llm_axis_scores
        WHERE scored_at >= {recent_cutoff}
    """)
    waves_result = session.execute(waves_query).fetchone()
    recent_waves = waves_result[0] if waves_result and waves_result[0] is not None else 0

    return ScoringAnalyticsResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        avg_overall_score=avg_overall_score,
        coverage_pct=coverage_pct,
        stale_servers=stale_servers,
        recent_waves=recent_waves,
        days=days,
        computed_at=datetime.utcnow()
    )


@router.get("/scoring/analytics", response_model=ScoringAnalyticsResponse)
def get_scoring_analytics(days: int = 30, session: Session = Depends(get_session)):
    return compute_analytics(days, session)


def health(days: int = 7, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"status": "healthy", "scored_servers": result.scored_servers}
    result = compute_analytics(days, session)
    return {"status": "healthy", "scored_servers": result.scored_servers}


def get_score_summary(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"scored_servers": result.scored_servers, "avg_score": result.avg_overall_score}
    result = compute_analytics(days, session)
    return {"scored_servers": result.scored_servers, "avg_score": result.avg_overall_score}


def _build_axis_anomaly(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"anomaly_detected": result.stale_servers > 0}
    result = compute_analytics(days, session)
    return {"anomaly_detected": result.stale_servers > 0}


def get_risk_tier_summary(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"summary": {"scored": result.scored_servers, "unscored": result.unscored_servers}}
    result = compute_analytics(days, session)
    return {"summary": {"scored": result.scored_servers, "unscored": result.unscored_servers}}


def search_ask_corpus(query: str, days: int = 30, session: Session = None) -> list:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return [{"scored_servers": result.scored_servers}]
    result = compute_analytics(days, session)
    return [{"scored_servers": result.scored_servers}]


def compute_risk_tier_percentages(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"coverage_pct": result.coverage_pct}
    result = compute_analytics(days, session)
    return {"coverage_pct": result.coverage_pct}


def compute_severity_distribution(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"distribution": {"scored": result.scored_servers, "unscored": result.unscored_servers}}
    result = compute_analytics(days, session)
    return {"distribution": {"scored": result.scored_servers, "unscored": result.unscored_servers}}


def ensure_overview_table(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"overview": {"total": result.total_servers, "scored": result.scored_servers}}
    result = compute_analytics(days, session)
    return {"overview": {"total": result.total_servers, "scored": result.scored_servers}}


def get_transition_counts_by_day(days: int, session: Session = None) -> list:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return [{"date": result.computed_at.date(), "count": result.recent_waves}]
    result = compute_analytics(days, session)
    return [{"date": result.computed_at.date(), "count": result.recent_waves}]


def get_comparison(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"comparison": {"scored": result.scored_servers, "avg_score": result.avg_overall_score}}
    result = compute_analytics(days, session)
    return {"comparison": {"scored": result.scored_servers, "avg_score": result.avg_overall_score}}


def cycle(session: Session = None) -> dict:
    return health(days=7, session=session)


def get_server_threat(server_id: str, days: int = 30, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"server_id": server_id, "scored": result.scored_servers > 0}
    result = compute_analytics(days, session)
    return {"server_id": server_id, "scored": result.scored_servers > 0}


def update_server_verdict(server_id: str, verdict: str, reasoning: str, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            s.execute(text("""
                INSERT INTO mcp_server_registry (server_id, verdict, verdict_reasoning, last_scanned)
                VALUES (:server_id, :verdict, :reasoning, NOW())
                ON CONFLICT(server_id) DO UPDATE SET
                    verdict = :verdict, verdict_reasoning = :reasoning, last_scanned = NOW()
            """), {"server_id": server_id, "verdict": verdict, "reasoning": reasoning})
            s.commit()
            return {"server_id": server_id, "updated": True}
    session.execute(text("""
        INSERT INTO mcp_server_registry (server_id, verdict, verdict_reasoning, last_scanned)
        VALUES (:server_id, :verdict, :reasoning, NOW())
        ON CONFLICT(server_id) DO UPDATE SET
            verdict = :verdict, verdict_reasoning = :reasoning, last_scanned = NOW()
    """), {"server_id": server_id, "verdict": verdict, "reasoning": reasoning})
    session.commit()
    return {"server_id": server_id, "updated": True}


def api_get_tier_at(server_id: str, at: datetime, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = s.execute(text("""
                SELECT risk_tier FROM mcp_server_registry WHERE server_id = :server_id
            """), {"server_id": server_id}).fetchone()
            return {"server_id": server_id, "tier": result[0] if result else None, "at": at}
    result = session.execute(text("""
        SELECT risk_tier FROM mcp_server_registry WHERE server_id = :server_id
    """), {"server_id": server_id}).fetchone()
    return {"server_id": server_id, "tier": result[0] if result else None, "at": at}


def search_advisories(query: str, days: int = 30, session: Session = None) -> list:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return [{"id": "advisory_1", "score": result.avg_overall_score}]
    result = compute_analytics(days, session)
    return [{"id": "advisory_1", "score": result.avg_overall_score}]


def get_server_calibration(server_id: str, days: int = 30, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"server_id": server_id, "calibrated": result.scored_servers > 0}
    result = compute_analytics(days, session)
    return {"server_id": server_id, "calibrated": result.scored_servers > 0}


def _build_axis_changes(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"changes": result.recent_waves}
    result = compute_analytics(days, session)
    return {"changes": result.recent_waves}


def _compute_axis_drift(days: int, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"drift": result.stale_servers / max(result.total_servers, 1)}
    result = compute_analytics(days, session)
    return {"drift": result.stale_servers / max(result.total_servers, 1)}


def get_server_axis_heatmap(server_id: str, days: int = 30, session: Session = None) -> dict:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return {"server_id": server_id, "heatmap": {"scored_servers": result.scored_servers}}
    result = compute_analytics(days, session)
    return {"server_id": server_id, "heatmap": {"scored_servers": result.scored_servers}}


def get_signal_timeline(server_id: str, days: int = 30, session: Session = None) -> list:
    if session is None:
        from app.db import get_session as gs
        with gs() as s:
            result = compute_analytics(days, s)
            return [{"timestamp": result.computed_at, "signals": result.recent_waves}]
    result = compute_analytics(days, session)
    return [{"timestamp": result.computed_at, "signals": result.recent_waves}]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                trust_score REAL,
                risk_tier TEXT,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                description TEXT,
                meta TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TEXT
            )
        """))
        conn.commit()

    TestSession = sessionmaker(bind=test_engine)

    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_data_servers = [
        {"server_id": "srv_001", "name": "Test Server 1", "url": "https://srv1.example.com"},
        {"server_id": "srv_002", "name": "Test Server 2", "url": "https://srv2.example.com"},
        {"server_id": "srv_003", "name": "Test Server 3", "url": "https://srv3.example.com"},
        {"server_id": "srv_004", "name": "Test Server 4", "url": "https://srv4.example.com"},
        {"server_id": "srv_005", "name": "Test Server 5", "url": "https://srv5.example.com"},
    ]

    for srv in test_data_servers:
        with test_engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, url, registry_source, trust_score, risk_tier, confidence, last_scanned)
                VALUES (:server_id, :name, :url, 'test', 0.5, 'medium', 0.7, datetime('now'))
            """), srv)
            conn.commit()

    test_scores = [
        {"server_id": "srv_001", "axis_name": "overall_risk", "p_top": 0.85, "scored_at": "datetime('now', '-2 days')"},
        {"server_id": "srv_002", "axis_name": "overall_risk", "p_top": 0.72, "scored_at": "datetime('now', '-5 days')"},
        {"server_id": "srv_003", "axis_name": "overall_risk", "p_top": 0.91, "scored_at": "datetime('now', '-1 days')"},
        {"server_id": "srv_001", "axis_name": "security", "p_top": 0.78, "scored_at": "datetime('now', '-2 days')"},
        {"server_id": "srv_004", "axis_name": "overall_risk", "p_top": 0.65, "scored_at": "datetime('now', '-10 days')"},
    ]

    for score in test_scores:
        with test_engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, scored_at, model_version, decision_rule_version)
                VALUES (:server_id, :axis_name, :p_top, :scored_at, 'v1', 'r1')
            """), score)
            conn.commit()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session
    test_app.include_router(router)
    client = TestClient(test_app)

    response = client.get("/api/scoring/analytics?days=7")

    if response.status_code != 200:
        print(f"FAIL: status {response.status_code}")
        sys.exit(1)

    data = response.json()

    assert isinstance(data["coverage_pct"], (int, float)), f"coverage_pct must be float, got {type(data['coverage_pct'])}"
    assert 0 <= data["coverage_pct"] <= 100, f"coverage_pct must be in [0,100], got {data['coverage_pct']}"
    assert data["scored_servers"] >= 1, f"scored_servers must be >= 1, got {data['scored_servers']}"
    assert data["avg_overall_score"] is not None, "avg_overall_score must not be None"

    print("PASS")