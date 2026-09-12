"""
server_axis_profile service - returns axis profile data for a server
"""
from typing import Dict, Any, List
import json

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


# 7 standard axis names
SEVEN_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def tier_from_score(p_top: float) -> str:
    """Compute risk tier from p_top score"""
    if p_top >= 0.7:
        return "critical"
    elif p_top >= 0.5:
        return "high"
    elif p_top >= 0.4:
        return "medium"
    elif p_top >= 0.2:
        return "low"
    else:
        return "minimal"


def get_axis_profile(server_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Get axis profile for a server.
    
    Reads all 7 axis rows from mcp_llm_axis_scores for the given server_id,
    joining to mcp_server_registry for server name and risk_tier.
    """
    # Query server info
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    server = session.execute(stmt).scalar_one_or_none()
    
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    # Query all axis scores for the server
    axes_stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    axis_rows = session.execute(axes_stmt).scalars().all()
    
    # Build axis list
    axes: List[Dict[str, Any]] = []
    for row in axis_rows:
        probs = row.probs
        if isinstance(probs, str):
            try:
                probs = json.loads(probs)
            except (json.JSONDecodeError, TypeError):
                probs = {}
        elif probs is None:
            probs = {}
        
        axes.append({
            "axis_name": row.axis_name,
            "label": row.label,
            "label_index": row.label_index,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "probs": probs,
            "escalated": bool(row.escalated) if row.escalated is not None else False,
            "decision_rule_version": row.decision_rule_version,
            "model_version": row.model_version,
            "adapter_sha256": row.adapter_sha256,
            "scored_at": row.scored_at,
        })
    
    # Compute overall
    overall_p_top = sum(a["p_top"] for a in axes) / len(axes) if axes else 0.0
    overall_tier = tier_from_score(overall_p_top)
    
    return {
        "server_id": server.server_id,
        "name": server.name,
        "risk_tier": server.risk_tier,
        "scored_at": server.scored_at,
        "overall": {
            "p_top": overall_p_top,
            "tier_from_score": overall_tier,
        },
        "axes": axes,
    }


def get_axis_profile_sql(server_id: str, cursor) -> Dict[str, Any]:
    """
    SQL-based axis profile query for self-testing with in-memory SQLite.
    Returns same structure as get_axis_profile() but uses raw SQL.
    """
    # Query server info
    cursor.execute(
        "SELECT server_id, name, risk_tier, scored_at FROM mcp_server_registry WHERE server_id = ?",
        (server_id,)
    )
    server_row = cursor.fetchone()
    
    if not server_row:
        raise ValueError(f"Server {server_id} not found")
    
    # Query all axis scores
    cursor.execute(
        """SELECT axis_name, label, label_index, p_top, p_critical, p_danger,
                  probs, escalated, decision_rule_version, model_version,
                  adapter_sha256, scored_at
           FROM mcp_llm_axis_scores
           WHERE server_id = ?
           ORDER BY id""",
        (server_id,)
    )
    axis_rows = cursor.fetchall()
    
    # Build axis list
    axes: List[Dict[str, Any]] = []
    for row in axis_rows:
        probs_str = row[6]
        try:
            probs = json.loads(probs_str) if probs_str else {}
        except (json.JSONDecodeError, TypeError):
            probs = {}
        
        axes.append({
            "axis_name": row[0],
            "label": row[1],
            "label_index": row[2],
            "p_top": row[3],
            "p_critical": row[4],
            "p_danger": row[5],
            "probs": probs,
            "escalated": bool(row[7]),
            "decision_rule_version": row[8],
            "model_version": row[9],
            "adapter_sha256": row[10],
            "scored_at": row[11],
        })
    
    # Compute overall
    overall_p_top = sum(a["p_top"] for a in axes) / len(axes) if axes else 0.0
    overall_tier = tier_from_score(overall_p_top)
    
    return {
        "server_id": server_row[0],
        "name": server_row[1],
        "risk_tier": server_row[2],
        "scored_at": server_row[3],
        "overall": {
            "p_top": overall_p_top,
            "tier_from_score": overall_tier,
        },
        "axes": axes,
    }


if __name__ == "__main__":
    import sqlite3
    
    # In-memory SQLite self-test
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # Create tables matching production schema
    cursor.execute("""
        CREATE TABLE mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            scored_at TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            label TEXT NOT NULL,
            label_index INTEGER NOT NULL,
            p_top REAL NOT NULL,
            p_critical REAL NOT NULL,
            p_danger REAL NOT NULL,
            probs TEXT NOT NULL,
            escalated INTEGER NOT NULL,
            decision_rule_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            adapter_sha256 TEXT NOT NULL,
            scored_at TEXT NOT NULL
        )
    """)
    
    # Seed test data
    server_id = "test_srv_001"
    scored_at = "2024-01-15T10:30:00"
    
    cursor.execute(
        "INSERT INTO mcp_server_registry (server_id, name, risk_tier, scored_at) VALUES (?, ?, ?, ?)",
        (server_id, "TestServer", "high", scored_at)
    )
    
    # Insert 7 axis rows
    axes_data = [
        ("overall_risk", "High Risk", 2, 0.75, 0.20, 0.05, '{"critical":0.20,"high":0.55,"medium":0.20,"low":0.05}', 1),
        ("auth_strength", "Strong Auth", 1, 0.15, 0.10, 0.75, '{"critical":0.10,"high":0.05,"medium":0.15,"low":0.70}', 0),
        ("capability_breadth", "Broad", 3, 0.60, 0.25, 0.15, '{"critical":0.25,"high":0.35,"medium":0.25,"low":0.15}', 1),
        ("data_sensitivity", "Sensitive", 3, 0.80, 0.15, 0.05, '{"critical":0.15,"high":0.65,"medium":0.15,"low":0.05}', 1),
        ("network_egress", "Limited", 1, 0.20, 0.15, 0.65, '{"critical":0.15,"high":0.05,"medium":0.20,"low":0.60}', 0),
        ("maintainer_trust", "Trusted", 0, 0.10, 0.05, 0.85, '{"critical":0.05,"high":0.05,"medium":0.10,"low":0.80}', 0),
        ("exploit_surface", "Large", 3, 0.70, 0.20, 0.10, '{"critical":0.20,"high":0.50,"medium":0.20,"low":0.10}', 1),
    ]
    
    for axis_data in axes_data:
        cursor.execute(
            """INSERT INTO mcp_llm_axis_scores
               (server_id, axis_name, label, label_index, p_top, p_critical, p_danger,
                probs, escalated, decision_rule_version, model_version, adapter_sha256, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (server_id,) + axis_data + ("v1.0", "gpt-4-0613", "abc123def456", scored_at)
        )
    
    conn.commit()
    
    # Run self-test
    try:
        result = get_axis_profile_sql(server_id, cursor)
        
        # Assert 200 response equivalent - data found
        assert result["server_id"] == server_id, f"server_id mismatch"
        assert result["name"] == "TestServer", f"name mismatch"
        assert result["risk_tier"] == "high", f"risk_tier mismatch"
        
        # Assert all 7 axes present
        assert len(result["axes"]) == 7, f"Expected 7 axes, got {len(result['axes'])}"
        
        # Assert known p_top value for overall_risk axis
        overall_risk_axis = next((a for a in result["axes"] if a["axis_name"] == "overall_risk"), None)
        assert overall_risk_axis is not None, "overall_risk axis not found"
        assert overall_risk_axis["p_top"] == 0.75, f"Expected p_top 0.75, got {overall_risk_axis['p_top']}"
        
        # Assert overall computed correctly
        expected_overall_p_top = sum(a[3] for a in axes_data) / 7  # average of p_top values
        assert abs(result["overall"]["p_top"] - expected_overall_p_top) < 0.001
        
        print("PASS")
        
    except AssertionError as e:
        print(f"FAIL: {e}")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        conn.close()