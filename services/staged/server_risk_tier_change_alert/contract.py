"""
server_risk_tier_change_alert service contract.
Detects when servers change risk tiers based on axis score deltas.
"""
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk"])


class TierChange(BaseModel):
    server_id: str
    server_name: str
    old_tier: str
    new_tier: str
    changed_at: datetime
    axis_drivers: list[str]


class TierChangesResponse(BaseModel):
    changes: list[TierChange]


def label_to_tier(label: str) -> str:
    """Map axis score label to risk tier."""
    if not label:
        return "unknown"
    label_lower = label.lower()
    if label_lower in ("critical", "high"):
        return "high"
    elif label_lower in ("medium", "elevated"):
        return "medium"
    elif label_lower in ("low", "safe", "info"):
        return "low"
    return "unknown"


def compute_server_tier_from_scores(scores: list[dict]) -> Optional[str]:
    """Compute overall risk tier from a list of axis scores."""
    if not scores:
        return None
    tier_counts = defaultdict(int)
    for score in scores:
        if score.get("label"):
            tier_counts[label_to_tier(score["label"])] += 1
    if not tier_counts:
        return None
    if tier_counts["high"] > 0:
        return "high"
    elif tier_counts["medium"] > 0:
        return "medium"
    return "low"


def get_tier_changes_for_server(
    server_id: str,
    session: Session
) -> Optional[tuple[datetime, str, str, list[str]]]:
    """
    Check if a server changed tier between its two most recent axis scores.
    Returns (changed_at, old_tier, new_tier, drivers) or None.
    """
    result = session.execute(
        text("""
            SELECT id, axis_name, label, scored_at
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
            LIMIT 2
        """),
        {"server_id": server_id}
    )
    rows = result.fetchall()
    if len(rows) < 2:
        return None
    
    new_score = {"label": rows[0].label, "axis_name": rows[0].axis_name}
    old_score = {"label": rows[1].label, "axis_name": rows[1].axis_name}
    
    new_tier = label_to_tier(new_score["label"])
    old_tier = label_to_tier(old_score["label"])
    
    if new_tier != old_tier:
        drivers = [new_score["axis_name"], old_score["axis_name"]]
        return (rows[0].scored_at, old_tier, new_tier, list(set(drivers)))
    
    return None


@router.get("/risk/tier-changes", response_model=TierChangesResponse)
def get_tier_changes(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session)
) -> TierChangesResponse:
    """
    Detect servers whose risk tier changed based on their most recent
    axis score compared to the prior one.
    """
    servers_result = session.execute(
        text("""
            SELECT server_id, name
            FROM mcp_server_registry
            ORDER BY last_assessed DESC
        """)
    )
    servers = {row.server_id: row.name for row in servers_result.fetchall()}
    
    changes = []
    for server_id in servers:
        tier_change = get_tier_changes_for_server(server_id, session)
        if tier_change:
            changed_at, old_tier, new_tier, drivers = tier_change
            changes.append(TierChange(
                server_id=server_id,
                server_name=servers[server_id],
                old_tier=old_tier,
                new_tier=new_tier,
                changed_at=changed_at,
                axis_drivers=drivers
            ))
    
    changes.sort(key=lambda x: x.changed_at, reverse=True)
    return TierChangesResponse(changes=changes[:limit])


def get_server(server_id: str, session: Session) -> Optional[dict]:
    """Get a server by ID."""
    result = session.execute(
        text("""
            SELECT server_id, name, url, risk_tier, last_assessed,
                   trust_score, confidence, description
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        return None
    return {
        "server_id": row.server_id,
        "name": row.name,
        "url": row.url,
        "risk_tier": row.risk_tier,
        "last_assessed": row.last_assessed,
        "trust_score": row.trust_score,
        "confidence": row.confidence,
        "description": row.description
    }


def get_axis_scores(server_id: str, session: Session) -> list[dict]:
    """Get all axis scores for a server."""
    result = session.execute(
        text("""
            SELECT id, server_id, axis_name, label, p_critical, p_danger,
                   p_top, scored_at, model_version, decision_rule_version
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
        """),
        {"server_id": server_id}
    )
    return [
        {
            "id": row.id,
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "label": row.label,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "p_top": row.p_top,
            "scored_at": row.scored_at,
            "model_version": row.model_version,
            "decision_rule_version": row.decision_rule_version
        }
        for row in result.fetchall()
    ]


def get_recent_servers(limit: int, session: Session) -> list[dict]:
    """Get recently assessed servers."""
    result = session.execute(
        text("""
            SELECT server_id, name, url, risk_tier, last_assessed
            FROM mcp_server_registry
            ORDER BY last_assessed DESC
            LIMIT :limit
        """),
        {"limit": limit}
    )
    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "url": row.url,
            "risk_tier": row.risk_tier,
            "last_assessed": row.last_assessed
        }
        for row in result.fetchall()
    ]


def read_server_score_history(server_id: str, session: Session) -> list[dict]:
    """Get complete score history for a server."""
    result = session.execute(
        text("""
            SELECT server_id, axis_name, label, scored_at
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at ASC
        """),
        {"server_id": server_id}
    )
    return [
        {
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "label": row.label,
            "scored_at": row.scored_at
        }
        for row in result.fetchall()
    ]


def get_risk_tier_summary(session: Session) -> dict:
    """Get summary of servers by risk tier."""
    result = session.execute(
        text("""
            SELECT risk_tier, COUNT(*) as count
            FROM mcp_server_registry
            WHERE risk_tier IS NOT NULL
            GROUP BY risk_tier
        """)
    )
    summary = {}
    for row in result.fetchall():
        tier = row.risk_tier or "unknown"
        summary[tier] = row.count
    return summary


def compare_perspectives(server_id: str, session: Session) -> dict:
    """Compare different scoring perspectives for a server."""
    result = session.execute(
        text("""
            SELECT axis_name, label, p_critical, p_danger, scored_at
            FROM mcp_llm_axis_scores
            WHERE server_id = :server_id
            ORDER BY scored_at DESC
        """),
        {"server_id": server_id}
    )
    scores = [
        {
            "axis_name": row.axis_name,
            "label": row.label,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "scored_at": row.scored_at
        }
        for row in result.fetchall()
    ]
    
    perspectives = defaultdict(list)
    for score in scores:
        perspectives[score["axis_name"]].append(score)
    
    return {
        "server_id": server_id,
        "perspectives": dict(perspectives),
        "latest_scores": scores[:10] if scores else []
    }


def get_mesh_scores(server_id: str, session: Session) -> list[dict]:
    """Get mesh/composite scores for a server."""
    return get_axis_scores(server_id, session)


def get_server_tier_from_registry(server_id: str, session: Session) -> Optional[str]:
    """Get current risk tier from registry."""
    result = session.execute(
        text("""
            SELECT risk_tier
            FROM mcp_server_registry
            WHERE server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    return row.risk_tier if row else None


def export_entity_report(session: Session, org_id: Optional[str] = None) -> list[dict]:
    """Export entity report data."""
    query = """
        SELECT server_id, name, url, risk_tier, trust_score, last_assessed
        FROM mcp_server_registry
    """
    params = {}
    if org_id:
        query += " WHERE org_id = :org_id"
        params["org_id"] = org_id
    result = session.execute(text(query), params)
    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "url": row.url,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
            "last_assessed": row.last_assessed
        }
        for row in result.fetchall()
    ]


def get_facet_enumeration(facet_name: str, session: Session) -> list[str]:
    """Enumerate values for a given facet."""
    column_map = {
        "risk_tier": "risk_tier",
        "axis_name": "axis_name",
        "label": "label"
    }
    col = column_map.get(facet_name)
    if not col:
        return []
    
    if col in ("axis_name", "label"):
        result = session.execute(
            text(f"SELECT DISTINCT {col} FROM mcp_llm_axis_scores ORDER BY {col}")
        )
    else:
        result = session.execute(
            text(f"SELECT DISTINCT {col} FROM mcp_server_registry WHERE {col} IS NOT NULL ORDER BY {col}")
        )
    return [row[0] for row in result.fetchall()]


def check_heartbeat(session: Session) -> bool:
    """Check if the service is healthy."""
    try:
        session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_perspectives_for_digest(server_ids: list[str], session: Session) -> dict:
    """Get perspectives for email digest."""
    if not server_ids:
        return {"servers": []}
    
    placeholders = ",".join([f":id{i}" for i in range(len(server_ids))])
    result = session.execute(
        text(f"""
            SELECT server_id, axis_name, label, scored_at
            FROM mcp_llm_axis_scores
            WHERE server_id IN ({placeholders})
            AND scored_at > NOW() - INTERVAL '7 days'
            ORDER BY server_id, scored_at DESC
        """),
        {f"id{i}": sid for i, sid in enumerate(server_ids)}
    )
    
    servers_data = defaultdict(list)
    for row in result.fetchall():
        servers_data[row.server_id].append({
            "axis_name": row.axis_name,
            "label": row.label,
            "scored_at": row.scored_at
        })
    
    return {
        "servers": [
            {"server_id": sid, "perspectives": servers_data.get(sid, [])}
            for sid in server_ids
        ]
    }


def get_never_scored_burndown(session: Session) -> dict:
    """Get burndown data for servers never scored."""
    result = session.execute(
        text("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN last_assessed IS NOT NULL THEN 1 ELSE 0 END) as assessed
            FROM mcp_server_registry
        """)
    )
    row = result.fetchone()
    return {
        "total_servers": row.total if row else 0,
        "assessed_servers": row.assessed if row else 0,
        "never_scored": (row.total - row.assessed) if row else 0
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                risk_tier TEXT,
                last_assessed TIMESTAMP,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                registry_source TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                meta TEXT,
                scan_count INTEGER,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT,
                label_index INTEGER,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                scored_at TIMESTAMP NOT NULL,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated BOOLEAN,
                escalated_to TEXT,
                adapter_sha256 TEXT
            )
        """))
    
    base_time = datetime(2025, 1, 2, 12, 0, 0)
    
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, risk_tier, last_assessed)
            VALUES
                ('srv-001', 'Server Alpha', 'high', :t1),
                ('srv-002', 'Server Beta', 'medium', :t1),
                ('srv-003', 'Server Gamma', 'critical', :t1)
        """), {"t1": base_time})
        
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at, p_critical, p_danger)
            VALUES
                ('srv-001', 'security', 'medium', :t0, 0.1, 0.3),
                ('srv-001', 'security', 'critical', :t1, 0.8, 0.15),
                ('srv-002', 'security', 'low', :t0, 0.05, 0.1),
                ('srv-002', 'security', 'medium', :t1, 0.2, 0.35),
                ('srv-003', 'security', 'critical', :t0, 0.75, 0.1),
                ('srv-003', 'security', 'critical', :t1, 0.85, 0.05)
        """), {"t0": base_time - timedelta(days=1), "t1": base_time})
    
    app.dependency_overrides[get_session] = lambda: Session(bind=engine)
    
    client = TestClient(app)
    response = client.get("/api/risk/tier-changes?limit=10")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    changes = data.get("changes", [])
    
    assert len(changes) == 2, f"Expected 2 tier changes, got {len(changes)}"
    
    for change in changes:
        assert change["old_tier"] != change["new_tier"], \
            f"Server {change['server_id']} has same tier: {change['old_tier']}"
    
    print("PASS")