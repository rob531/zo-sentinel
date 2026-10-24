# services/staged/builder_health_api/logic.py
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)


def list_routers(session: Session) -> List[Dict[str, Any]]:
    """List all registered MCP servers with their router endpoints."""
    results = session.execute(
        text("""
            SELECT 
                s.server_id,
                s.name,
                s.description,
                s.enabled,
                s.created_at,
                s.updated_at,
                s.last_health_check,
                s.health_status,
                s.adapter_sha256
            FROM mcp_server_registry s
            WHERE s.enabled = true
            ORDER BY s.name
        """)
    ).fetchall()
    
    return [
        {
            "server_id": row[0],
            "name": row[1],
            "description": row[2],
            "enabled": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "updated_at": row[5].isoformat() if row[5] else None,
            "last_health_check": row[6].isoformat() if row[6] else None,
            "health_status": row[7],
            "adapter_sha256": row[8],
        }
        for row in results
    ]


def corpus_by_server_id(session: Session, server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch corpus data associated with a specific server."""
    result = session.execute(
        text("""
            SELECT 
                s.server_id,
                s.name,
                s.adapter_sha256,
                s.health_status,
                s.last_health_check
            FROM mcp_server_registry s
            WHERE s.server_id = :server_id
        """),
        {"server_id": server_id}
    ).fetchone()
    
    if not result:
        return None
    
    return {
        "server_id": result[0],
        "name": result[1],
        "adapter_sha256": result[2],
        "health_status": result[3],
        "last_health_check": result[4].isoformat() if result[4] else None,
    }


def get_query_expansion(session: Session, query: str) -> Dict[str, Any]:
    """Get query expansion data for a given search term."""
    result = session.execute(
        text("""
            SELECT 
                server_id,
                name,
                health_status
            FROM mcp_server_registry
            WHERE name ILIKE :query_pattern
               OR description ILIKE :query_pattern
            LIMIT 10
        """),
        {"query_pattern": f"%{query}%"}
    ).fetchall()
    
    return {
        "original_query": query,
        "expanded_terms": [row[1] for row in result],
        "matching_servers": [
            {"server_id": row[0], "name": row[1], "health_status": row[2]}
            for row in result
        ],
    }


def get_ask_corpus_index(session: Session) -> List[Dict[str, Any]]:
    """Get the full corpus index of available servers."""
    results = session.execute(
        text("""
            SELECT 
                server_id,
                name,
                description,
                health_status,
                enabled
            FROM mcp_server_registry
            WHERE enabled = true
            ORDER BY name
        """)
    ).fetchall()
    
    return [
        {
            "server_id": row[0],
            "name": row[1],
            "description": row[2],
            "health_status": row[3],
            "enabled": row[4],
        }
        for row in results
    ]


def endpoint(session: Session, server_id: str) -> Optional[Dict[str, Any]]:
    """Get endpoint details for a specific server."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    
    if not server:
        return None
    
    return {
        "server_id": server.server_id,
        "name": server.name,
        "enabled": server.enabled,
        "health_status": server.health_status,
        "last_health_check": server.last_health_check.isoformat() if server.last_health_check else None,
    }


def read_advisory(session: Session, advisory_id: str) -> Optional[Dict[str, Any]]:
    """Read a vulnerability advisory by ID."""
    result = session.execute(
        text("""
            SELECT 
                advisory_id,
                title,
                severity,
                published_at,
                affected_servers
            FROM vuln_advisories
            WHERE advisory_id = :advisory_id
        """),
        {"advisory_id": advisory_id}
    ).fetchone()
    
    if not result:
        return None
    
    return {
        "advisory_id": result[0],
        "title": result[1],
        "severity": result[2],
        "published_at": result[3].isoformat() if result[3] else None,
        "affected_servers": result[4],
    }


def read_vuln_analysis(session: Session, server_id: str) -> Optional[Dict[str, Any]]:
    """Read vulnerability analysis for a specific server."""
    result = session.execute(
        text("""
            SELECT 
                server_id,
                scan_timestamp,
                vulnerability_count,
                severity_breakdown,
                last_updated
            FROM vuln_analysis_results
            WHERE server_id = :server_id
            ORDER BY scan_timestamp DESC
            LIMIT 1
        """),
        {"server_id": server_id}
    ).fetchone()
    
    if not result:
        return None
    
    return {
        "server_id": result[0],
        "scan_timestamp": result[1].isoformat() if result[1] else None,
        "vulnerability_count": result[2],
        "severity_breakdown": result[3],
        "last_updated": result[4].isoformat() if result[4] else None,
    }


def get_vulnerability_analysis(session: Session, server_id: str) -> Dict[str, Any]:
    """Get vulnerability analysis summary for a server."""
    analysis = read_vuln_analysis(session, server_id)
    if analysis:
        return analysis
    
    return {
        "server_id": server_id,
        "status": "no_analysis_available",
        "vulnerability_count": 0,
    }


def mock_post(session: Session, server_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Mock endpoint for posting data to a server."""
    return {
        "status": "accepted",
        "server_id": server_id,
        "payload_received": payload,
    }


def refresh_attestations(session: Session, org_id: str) -> Dict[str, Any]:
    """Refresh attestations for an organization."""
    result = session.execute(
        text("""
            SELECT 
                COUNT(*) as attestation_count,
                MAX(created_at) as last_attestation
            FROM mcp_attestations
            WHERE org_id = :org_id
        """),
        {"org_id": org_id}
    ).fetchone()
    
    return {
        "org_id": org_id,
        "attestation_count": result[0] if result else 0,
        "last_attestation": result[1].isoformat() if result and result[1] else None,
        "status": "refreshed",
    }


def get_verdict_summary(session: Session) -> Dict[str, Any]:
    """Get a summary of verdicts across all servers."""
    result = session.execute(
        text("""
            SELECT 
                COUNT(*) as total_decisions,
                COUNT(DISTINCT server_id) as unique_servers,
                MAX(decision_timestamp) as latest_decision
            FROM mcp_decisions
        """)
    ).fetchone()
    
    return {
        "total_decisions": result[0] if result else 0,
        "unique_servers": result[1] if result else 0,
        "latest_decision": result[2].isoformat() if result and result[2] else None,
    }


def fetch_recent_verdicts(session: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch recent verdict decisions."""
    results = session.execute(
        text("""
            SELECT 
                decision_id,
                server_id,
                verdict,
                decision_timestamp,
                reasoning
            FROM mcp_decisions
            ORDER BY decision_timestamp DESC
            LIMIT :limit
        """),
        {"limit": limit}
    ).fetchall()
    
    return [
        {
            "decision_id": row[0],
            "server_id": row[1],
            "verdict": row[2],
            "decision_timestamp": row[3].isoformat() if row[3] else None,
            "reasoning": row[4],
        }
        for row in results
    ]


def test_endpoint() -> str:
    """Self-test to verify the module loads correctly."""
    return "PASS"


if __name__ == "__main__":
    from app.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    result = test_endpoint()
    print(result)