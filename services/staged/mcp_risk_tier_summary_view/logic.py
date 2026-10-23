"""
logic.py - Risk tier summary view for MCP servers

Provides risk tier classification and summary views for the MCP server registry.
"""
from typing import Optional
from collections import defaultdict

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


def get_risk_tier_thresholds() -> dict:
    """
    Returns the risk tier classification thresholds based on axis scores.
    
    Returns:
        dict: Threshold configuration for each risk tier
    """
    return {
        "critical": 0.0,      # score < 0.3
        "high": 0.3,          # 0.3 <= score < 0.5  
        "medium": 0.5,        # 0.5 <= score < 0.7
        "low": 0.7,           # 0.7 <= score < 0.85
        "minimal": 0.85,      # score >= 0.85
    }


def classify_risk_tier(score: Optional[float]) -> str:
    """
    Classifies a server into a risk tier based on its composite score.
    
    Args:
        score: The composite risk score (0.0 to 1.0), or None if unavailable
        
    Returns:
        str: Risk tier classification ('critical', 'high', 'medium', 'low', 'minimal')
    """
    if score is None:
        return "unknown"
    
    thresholds = get_risk_tier_thresholds()
    
    if score < thresholds["high"]:
        return "critical"
    elif score < thresholds["medium"]:
        return "high"
    elif score < thresholds["low"]:
        return "medium"
    elif score < thresholds["minimal"]:
        return "low"
    else:
        return "minimal"


def compute_composite_score(axis_scores: dict) -> Optional[float]:
    """
    Computes a composite risk score from individual axis scores.
    
    Args:
        axis_scores: Dict of axis name to score value
        
    Returns:
        Optional[float]: Weighted composite score, or None if no valid scores
    """
    if not axis_scores:
        return None
    
    # Weight configuration for risk assessment
    weights = {
        "security": 0.35,
        "reliability": 0.25,
        "compliance": 0.20,
        "performance": 0.10,
        "maintenance": 0.10,
    }
    
    total_weight = 0.0
    weighted_sum = 0.0
    
    for axis, weight in weights.items():
        score = axis_scores.get(axis)
        if score is not None:
            weighted_sum += score * weight
            total_weight += weight
    
    if total_weight == 0:
        return None
    
    return weighted_sum / total_weight


def get_servers_by_tier(org_id: int) -> dict:
    """
    Retrieves all MCP servers for an organization, grouped by risk tier.
    
    Args:
        org_id: The organization ID
        
    Returns:
        dict: Servers grouped by risk tier
    """
    with get_session() as session:
        servers = session.query(McpServerRegistry).filter(
            McpServerRegistry.org_id == org_id
        ).all()
        
        # Get latest axis scores for each server
        server_ids = [s.id for s in servers]
        
        axis_scores = {}
        if server_ids:
            scores = session.query(McpLlmAxisScore).filter(
                McpLlmAxisScore.server_id.in_(server_ids)
            ).order_by(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.created_at.desc()
            ).all()
            
            # Group by server, take latest
            for score in scores:
                if score.server_id not in axis_scores:
                    axis_scores[score.server_id] = {
                        "security": score.security_score,
                        "reliability": score.reliability_score,
                        "compliance": score.compliance_score,
                        "performance": score.performance_score,
                        "maintenance": score.maintenance_score,
                    }
        
        # Group servers by tier
        tiers = defaultdict(list)
        
        for server in servers:
            scores = axis_scores.get(server.id, {})
            composite = compute_composite_score(scores)
            tier = classify_risk_tier(composite)
            
            tiers[tier].append({
                "server_id": server.id,
                "name": server.name,
                "endpoint": server.endpoint,
                "composite_score": composite,
                "axis_scores": scores,
            })
        
        return dict(tiers)


def get_tier_summary(org_id: int) -> dict:
    """
    Generates a summary of servers by risk tier with counts and averages.
    
    Args:
        org_id: The organization ID
        
    Returns:
        dict: Summary statistics by tier
    """
    servers_by_tier = get_servers_by_tier(org_id)
    
    summary = {}
    
    for tier in ["critical", "high", "medium", "low", "minimal", "unknown"]:
        servers = servers_by_tier.get(tier, [])
        count = len(servers)
        
        scores = [s["composite_score"] for s in servers if s["composite_score"] is not None]
        avg_score = sum(scores) / len(scores) if scores else None
        
        summary[tier] = {
            "count": count,
            "average_score": round(avg_score, 3) if avg_score is not None else None,
            "servers": [s["name"] for s in servers],
        }
    
    return summary


def get_tier_trend(org_id: int, server_id: Optional[int] = None) -> dict:
    """
    Retrieves score history for tier classification changes.
    
    Args:
        org_id: The organization ID
        server_id: Optional specific server ID
        
    Returns:
        dict: Trend data for tier changes
    """
    with get_session() as session:
        query = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.org_id == org_id
        )
        
        if server_id:
            query = query.filter(McpLlmAxisScore.server_id == server_id)
        
        scores = query.order_by(McpLlmAxisScore.created_at.asc()).all()
        
        trends = []
        for score in scores:
            axis_dict = {
                "security": score.security_score,
                "reliability": score.reliability_score,
                "compliance": score.compliance_score,
                "performance": score.performance_score,
                "maintenance": score.maintenance_score,
            }
            composite = compute_composite_score(axis_dict)
            
            trends.append({
                "server_id": score.server_id,
                "timestamp": score.created_at.isoformat(),
                "composite_score": composite,
                "tier": classify_risk_tier(composite),
            })
        
        return {"trends": trends}


if __name__ == "__main__":
    # Self-test
    print("Running logic.py self-test...")
    
    # Test threshold configuration
    thresholds = get_risk_tier_thresholds()
    assert "critical" in thresholds
    assert "high" in thresholds
    assert "medium" in thresholds
    assert "low" in thresholds
    assert "minimal" in thresholds
    
    # Test tier classification
    assert classify_risk_tier(None) == "unknown"
    assert classify_risk_tier(0.1) == "critical"
    assert classify_risk_tier(0.3) == "high"
    assert classify_risk_tier(0.5) == "medium"
    assert classify_risk_tier(0.7) == "low"
    assert classify_risk_tier(0.9) == "minimal"
    
    # Test composite score computation
    assert compute_composite_score({}) is None
    assert compute_composite_score({"security": 0.8}) is not None
    
    # Test with realistic axis scores
    axis_scores = {
        "security": 0.75,
        "reliability": 0.80,
        "compliance": 0.70,
        "performance": 0.85,
        "maintenance": 0.90,
    }
    composite = compute_composite_score(axis_scores)
    assert composite is not None
    assert 0.0 <= composite <= 1.0
    assert classify_risk_tier(composite) in ["low", "medium", "high", "critical", "minimal", "unknown"]
    
    print("PASS: All self-tests passed")