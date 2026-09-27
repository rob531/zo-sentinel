"""Scoring summary service - computes aggregate statistics for each axis."""
from typing import List
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class AxisSummary(BaseModel):
    axis_name: str
    count: int
    avg: float | None
    min: float | None
    max: float | None


class ScoringSummaryResponse(BaseModel):
    summary: List[AxisSummary]


def get_scoring_summary(db: Session) -> List[AxisSummary]:
    """Get scoring summary statistics for each axis."""
    results = db.execute(
        text("""
            SELECT 
                axis_name,
                COUNT(*) as count,
                AVG(p_danger) as avg,
                MIN(p_danger) as min,
                MAX(p_danger) as max
            FROM mcp_llm_axis_scores
            GROUP BY axis_name
        """)
    ).fetchall()
    
    return [AxisSummary(
        axis_name=row.axis_name,
        count=row.count,
        avg=row.avg,
        min=row.min,
        max=row.max
    ) for row in results]


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                trust_score REAL,
                risk_tier TEXT,
                confidence REAL,
                description TEXT,
                meta TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                model_version TEXT,
                adapter_sha256 TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                decision_rule_version TEXT,
                scored_at TEXT,
                escalated INTEGER,
                escalated_to TEXT
            )
        """))
        conn.commit()
    
    Session = sessionmaker(bind=engine)
    
    from app.main import app
    from app.db import get_session
    
    def override_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    with Session() as session:
        for i in range(100):
            session.execute(
                text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)"),
                {"id": i + 1, "name": f"server_{i + 1}"}
            )
        
        axes = ["security", "reliability", "maintenance", "compliance"]
        for i in range(200):
            axis = axes[i % len(axes)]
            p_danger = 0.1 + (i % 10) * 0.09
            session.execute(
                text("""
                    INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, p_danger, label, scored_at)
                    VALUES (:server_id, :axis_name, :p_danger, :label, :scored_at)
                """),
                {
                    "server_id": (i % 100) + 1,
                    "axis_name": axis,
                    "p_danger": p_danger,
                    "label": "test",
                    "scored_at": "2024-01-01T00:00:00Z"
                }
            )
        session.commit()
        
        summary = get_scoring_summary(session)
        
        assert len(summary) == 4, f"Expected 4 axes, got {len(summary)}"
        
        axis_counts = {s.axis_name: s.count for s in summary}
        for axis in axes:
            assert axis in axis_counts, f"Missing axis: {axis}"
            assert axis_counts[axis] == 50, f"Axis {axis} expected 50, got {axis_counts[axis]}"
        
        for s in summary:
            assert s.avg is not None, f"avg is None for {s.axis_name}"
            assert s.min is not None, f"min is None for {s.axis_name}"
            assert s.max is not None, f"max is None for {s.axis_name}"
        
        print("PASS")
    
    app.dependency_overrides.clear()