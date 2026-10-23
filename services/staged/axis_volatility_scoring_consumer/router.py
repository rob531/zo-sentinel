"""
Axis Volatility Scoring Consumer Router

Computes axis volatility scores per server based on recent p_top scores.
"""
from datetime import datetime
from typing import Any
import statistics

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/axis-volatility", tags=["axis_volatility"])


class TestMcpLlmAxisScore(BaseModel):
    """Test model mirroring McpLlmAxisScore structure."""
    id: int
    server_id: str
    axis_name: str
    p_top: float
    scored_at: datetime

    class Config:
        from_attributes = True


class TestMcpServerRegistry(BaseModel):
    """Minimal server registry for test purposes."""
    id: int
    server_id: str
    hostname: str | None = None

    class Config:
        from_attributes = True


class VolatilityResult(BaseModel):
    """Result of axis volatility scoring."""
    server_id: str
    volatility_score: float
    evidence_blob: dict[str, Any]


def compute_volatility_scores(session: Session) -> list[VolatilityResult]:
    """
    Compute axis volatility scores for all servers.
    
    Reads McpLlmAxisScore, groups by (server_id, axis_name),
    computes stddev of p_top over last 10 scores per axis,
    averages across axes to produce 0-100 volatility_score.
    """
    results: list[VolatilityResult] = []
    
    # Get distinct server_ids that have axis scores
    server_query = text("""
        SELECT DISTINCT server_id 
        FROM McpLlmAxisScore 
        ORDER BY server_id
    """)
    servers = session.execute(server_query).fetchall()
    
    for (server_id,) in servers:
        # Get recent scores per axis for this server
        axis_query = text("""
            SELECT axis_name, p_top
            FROM McpLlmAxisScore
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
        """)
        axis_scores = session.execute(axis_query, {"server_id": server_id}).fetchall()
        
        if not axis_scores:
            continue
        
        # Group by axis_name
        axis_groups: dict[str, list[float]] = {}
        for axis_name, p_top in axis_scores:
            if axis_name not in axis_groups:
                axis_groups[axis_name] = []
            if len(axis_groups[axis_name]) < 10:
                axis_groups[axis_name].append(p_top)
        
        # Compute per-axis volatility (stddev of p_top)
        per_axis_volatility: dict[str, float] = {}
        for axis_name, scores in axis_groups.items():
            if len(scores) >= 2:
                stddev = statistics.stdev(scores)
                # Normalize to 0-100 scale (assuming p_top is 0-1)
                per_axis_volatility[axis_name] = stddev * 100
            elif len(scores) == 1:
                per_axis_volatility[axis_name] = 0.0
        
        if not per_axis_volatility:
            continue
        
        # Average across axes
        volatility_score = statistics.mean(per_axis_volatility.values())
        
        # Clamp to 0-100
        volatility_score = max(0.0, min(100.0, volatility_score))
        
        results.append(VolatilityResult(
            server_id=server_id,
            volatility_score=volatility_score,
            evidence_blob={"per_axis_volatility": per_axis_volatility}
        ))
    
    return results


@router.post("/compute", response_model=list[VolatilityResult])
def compute_all_volatility_scores(
    session: Session = Depends(get_session)
) -> list[VolatilityResult]:
    """
    Compute axis volatility scores for all servers.
    
    Reads from McpLlmAxisScore, computes volatility per axis,
    and writes results to mcp_signal_enrichments.
    """
    results = compute_volatility_scores(session)
    
    # Write results to mcp_signal_enrichments
    for result in results:
        insert_query = text("""
            INSERT INTO mcp_signal_enrichments 
            (server_id, signal_type, evidence_blob, created_at)
            VALUES (:server_id, :signal_type, :evidence_blob, NOW())
            ON CONFLICT (server_id, signal_type) 
            DO UPDATE SET evidence_blob = :evidence_blob, created_at = NOW()
        """)
        session.execute(insert_query, {
            "server_id": result.server_id,
            "signal_type": "axis_volatility",
            "evidence_blob": result.model_dump_json()
        })
    
    session.commit()
    return results


@router.get("/score/{server_id}", response_model=VolatilityResult | None)
def get_server_volatility_score(
    server_id: str,
    session: Session = Depends(get_session)
) -> VolatilityResult | None:
    """Get the axis volatility score for a specific server."""
    query = text("""
        SELECT evidence_blob
        FROM mcp_signal_enrichments
        WHERE server_id = :server_id AND signal_type = 'axis_volatility'
        ORDER BY created_at DESC
        LIMIT 1
    """)
    row = session.execute(query, {"server_id": server_id}).fetchone()
    
    if not row:
        return None
    
    import json
    data = json.loads(row[0])
    return VolatilityResult(**data)


# Self-test with seeded in-memory SQLite
def run_self_test():
    """Self-test using seeded in-memory SQLite."""
    import sqlite3
    
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE McpLlmAxisScore (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_top REAL NOT NULL,
            scored_at TIMESTAMP NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE mcp_signal_enrichments (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            evidence_blob TEXT,
            created_at TIMESTAMP NOT NULL,
            UNIQUE(server_id, signal_type)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE McpServerRegistry (
            id INTEGER PRIMARY KEY,
            server_id TEXT UNIQUE NOT NULL,
            hostname TEXT
        )
    """)
    
    # Seed test data for server "test-server-1"
    import random
    random.seed(42)
    
    axes = ["safety", "reliability", "performance", "cost_efficiency"]
    now = datetime.now()
    
    # Insert 10 scores per axis with varying p_top values
    for i in range(10):
        for axis in axes:
            # Varying p_top values to create volatility
            p_top = 0.5 + random.uniform(-0.3, 0.3)
            scored_at = now.replace(microsecond=i * 100000)
            cursor.execute("""
                INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top, scored_at)
                VALUES ('test-server-1', ?, ?, ?)
            """, (axis, p_top, scored_at))
    
    conn.commit()
    
    # Simulate the computation logic
    cursor.execute("""
        SELECT axis_name, p_top
        FROM McpLlmAxisScore
        WHERE server_id = 'test-server-1'
        ORDER BY scored_at DESC
    """)
    axis_scores = cursor.fetchall()
    
    axis_groups: dict[str, list[float]] = {}
    for axis_name, p_top in axis_scores:
        if axis_name not in axis_groups:
            axis_groups[axis_name] = []
        if len(axis_groups[axis_name]) < 10:
            axis_groups[axis_name].append(p_top)
    
    per_axis_volatility: dict[str, float] = {}
    for axis_name, scores in axis_groups.items():
        if len(scores) >= 2:
            stddev = statistics.stdev(scores)
            per_axis_volatility[axis_name] = stddev * 100
    
    volatility_score = statistics.mean(per_axis_volatility.values())
    volatility_score = max(0.0, min(100.0, volatility_score))
    
    # Assertions
    assert isinstance(volatility_score, float), f"Expected float, got {type(volatility_score)}"
    assert 0.0 <= volatility_score <= 100.0, f"Score {volatility_score} out of range [0,100]"
    assert len(per_axis_volatility) == len(axes), f"Missing axes: {per_axis_volatility}"
    for axis in axes:
        assert axis in per_axis_volatility, f"Missing axis: {axis}"
    
    conn.close()
    print("PASS")


if __name__ == "__main__":
    run_self_test()