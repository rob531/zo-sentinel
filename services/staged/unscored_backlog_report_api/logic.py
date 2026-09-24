from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine, func, or_, text
from sqlalchemy.orm import Session, sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


def get_unscored_backlog(
    session: Session,
    threshold_days: int = 30,
    limit: int = 50
) -> list[dict]:
    threshold_date = datetime.now(timezone.utc) - timedelta(days=threshold_days)
    
    subq = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label('max_scored_at')
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    
    results = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.trust_score,
            subq.c.max_scored_at.label('last_scored')
        )
        .outerjoin(subq, McpServerRegistry.server_id == subq.c.server_id)
        .filter(
            or_(
                subq.c.max_scored_at.is_(None),
                subq.c.max_scored_at < threshold_date
            )
        )
        .order_by(McpServerRegistry.trust_score.asc())
        .limit(limit)
        .all()
    )
    
    servers = []
    for row in results:
        days_since = None
        if row.last_scored:
            days_since = (datetime.now(timezone.utc) - row.last_scored).days
        servers.append({
            "server_id": row.server_id,
            "name": row.name,
            "trust_score": row.trust_score,
            "last_scored": row.last_scored,
            "days_since_score": days_since
        })
    
    return servers


if __name__ == "__main__":
    from fastapi import FastAPI
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine)
    ScopedSession = scoped_session(session_factory)
    
    with ScopedSession() as session:
        session.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                name VARCHAR,
                trust_score FLOAT,
                description TEXT,
                url VARCHAR,
                registry_source VARCHAR,
                risk_tier VARCHAR,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                meta TEXT,
                verdict VARCHAR,
                verdict_reasoning TEXT,
                scan_count INTEGER,
                confidence FLOAT,
                last_assessed TIMESTAMP
            )
        """))
        
        session.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                adapter_sha256 VARCHAR,
                model_version VARCHAR,
                axis_name VARCHAR,
                label VARCHAR,
                label_index INTEGER,
                p_critical FLOAT,
                p_danger FLOAT,
                p_top FLOAT,
                probs TEXT,
                scored_at TIMESTAMP,
                decision_rule_version VARCHAR,
                escalated BOOLEAN,
                escalated_to VARCHAR
            )
        """))
        
        session.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, trust_score)
            VALUES (1, 'unscored_server_1', 0.5),
                   (2, 'unscored_server_2', 0.3),
                   (3, 'scored_server_1', 0.7),
                   (4, 'scored_server_2', 0.8),
                   (5, 'scored_server_3', 0.9)
        """))
        
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (id, server_id, scored_at)
            VALUES (1, 3, :yesterday), (2, 4, :yesterday), (3, 5, :yesterday)
        """), {"yesterday": yesterday})
        
        session.commit()
        
        app = FastAPI()
        
        def override_get_session():
            try:
                yield ScopedSession()
            finally:
                pass
        
        app.dependency_overrides[get_session] = override_get_session
        
        from services.staged.unscored_backlog_report_api.logic import get_unscored_backlog
        with ScopedSession() as s:
            result = get_unscored_backlog(s, threshold_days=30, limit=50)
        
        assert len(result) == 2, f"Expected 2 servers, got {len(result)}"
        assert result[0]["server_id"] == 2
        assert result[1]["server_id"] == 1
        
        ScopedSession.remove()
        engine.dispose()
        
        print("PASS")