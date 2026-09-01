"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""
import json
import sys
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpLlmAxisScore, MCPSignalScores, MeshMemory


def get_mesh_memory(
    org_id: int,
    session: Optional[Session] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve mesh memory for an organization from the ZoComputer store."""
    query_payload = {
        "sql": """
            SELECT data
            FROM mesh_memory
            WHERE org_id = :org_id
            ORDER BY created_at DESC
            LIMIT 1
        """,
        "params": {"org_id": org_id}
    }
    
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json=query_payload,
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    response.raise_for_status()
    results = response.json()
    
    if results:
        return results[0].get("data")
    return None


def get_signal_scores(
    org_id: int,
    session: Optional[Session] = None
) -> List[Dict[str, Any]]:
    """Retrieve signal scores for an organization from app tables."""
    if session is None:
        raise ValueError("session required for app table access")
    
    rows = (
        session.query(MCPSignalScores)
        .filter(MCPSignalScores.org_id == org_id)
        .order_by(MCPSignalScores.created_at.desc())
        .all()
    )
    
    return [
        {
            "signal_name": r.signal_name,
            "score": r.score,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def get_mesh_scores(
    org_id: int,
    session: Optional[Session] = None
) -> List[Dict[str, Any]]:
    """Retrieve mesh scores for an organization from app tables."""
    if session is None:
        raise ValueError("session required for app table access")
    
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.org_id == org_id)
        .order_by(McpLlmAxisScore.created_at.desc())
        .all()
    )
    
    return [
        {
            "axis": r.axis,
            "score": r.score,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def setup_database(session: Session) -> None:
    """Initialize database schema for staged services."""
    session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS mesh_memory (
                id SERIAL PRIMARY KEY,
                org_id INTEGER NOT NULL,
                data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    )
    
    session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS mcp_signal_scores (
                id SERIAL PRIMARY KEY,
                org_id INTEGER NOT NULL,
                signal_name VARCHAR(255) NOT NULL,
                score FLOAT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    )
    
    session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
                id SERIAL PRIMARY KEY,
                org_id INTEGER NOT NULL,
                axis VARCHAR(255) NOT NULL,
                score FLOAT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    )
    
    session.commit()


def main() -> None:
    """Self-test: validate module compiles and core functions are callable."""
    engine = create_engine("sqlite:///:memory:")
    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()
    
    setup_database(test_session)
    
    test_session.execute(
        text("INSERT INTO mesh_memory (org_id, data) VALUES (:org_id, :data)"),
        {"org_id": 1, "data": json.dumps({"test": "mesh_memory_value"})}
    )
    test_session.execute(
        text(
            "INSERT INTO mcp_signal_scores (org_id, signal_name, score) "
            "VALUES (:org_id, :signal_name, :score)"
        ),
        {"org_id": 1, "signal_name": "test_signal", "score": 0.85}
    )
    test_session.execute(
        text(
            "INSERT INTO McpLlmAxisScore (org_id, axis, score) "
            "VALUES (:org_id, :axis, :score)"
        ),
        {"org_id": 1, "axis": "test_axis", "score": 0.72}
    )
    test_session.commit()
    
    mesh_mem = get_mesh_memory(org_id=1, session=test_session)
    assert mesh_mem is not None, "mesh_memory lookup failed"
    assert mesh_mem.get("test") == "mesh_memory_value"
    
    signal_scores = get_signal_scores(org_id=1, session=test_session)
    assert len(signal_scores) == 1
    assert signal_scores[0]["signal_name"] == "test_signal"
    assert signal_scores[0]["score"] == 0.85
    
    mesh_scores = get_mesh_scores(org_id=1, session=test_session)
    assert len(mesh_scores) == 1
    assert mesh_scores[0]["axis"] == "test_axis"
    assert mesh_scores[0]["score"] == 0.72
    
    test_session.close()
    print("PASS")


if __name__ == "__main__":
    main()