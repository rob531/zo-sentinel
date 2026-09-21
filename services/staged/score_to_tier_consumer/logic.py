from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def compute_risk_tier(
    p_critical: Optional[float],
    p_danger: Optional[float],
    p_top: Optional[float],
    confidence: Optional[float] = None
) -> str:
    """Compute risk tier based on axis score probabilities."""
    if p_critical is None and p_danger is None and p_top is None:
        return "unknown"
    
    p_crit = p_critical or 0.0
    p_dang = p_danger or 0.0
    p_t = p_top or 0.0
    
    if p_crit >= 0.5:
        return "critical"
    if p_crit >= 0.2:
        return "high"
    if p_dang >= 0.5:
        return "high"
    if p_dang >= 0.2:
        return "medium"
    if p_t >= 0.7:
        return "medium"
    if p_t >= 0.4:
        return "low"
    return "minimal"


def consume(session: Session, scored_at: Optional[datetime] = None) -> dict:
    """
    Process axis scores and update server risk tiers.
    
    Reads unprocessed axis scores, computes risk tier per server,
    and updates mcp_server_registry.risk_tier.
    """
    if scored_at is None:
        scored_at = datetime.now(timezone.utc)
    
    result = {
        "processed": 0,
        "updated": 0,
        "errors": 0,
        "servers": {}
    }
    
    try:
        latest_scores_query = (
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.p_danger,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.scored_at,
                McpLlmAxisScore.axis_name
            )
            .where(McpLlmAxisScore.scored_at <= scored_at)
            .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at.desc())
        )
        
        scores = session.execute(latest_scores_query).fetchall()
        
        server_latest = {}
        for score in scores:
            server_id = score.server_id
            if server_id not in server_latest:
                server_latest[server_id] = {
                    "p_critical": score.p_critical,
                    "p_danger": score.p_danger,
                    "p_top": score.p_top,
                    "scored_at": score.scored_at
                }
            result["processed"] += 1
        
        for server_id, score_data in server_latest.items():
            try:
                tier = compute_risk_tier(
                    score_data["p_critical"],
                    score_data["p_danger"],
                    score_data["p_top"]
                )
                
                update_stmt = (
                    update(McpServerRegistry)
                    .where(McpServerRegistry.server_id == server_id)
                    .values(
                        risk_tier=tier,
                        last_assessed=score_data["scored_at"]
                    )
                )
                
                session.execute(update_stmt)
                result["updated"] += 1
                result["servers"][server_id] = {
                    "risk_tier": tier,
                    "last_assessed": score_data["scored_at"].isoformat() if score_data["scored_at"] else None
                }
                
            except Exception as e:
                result["errors"] += 1
        
        session.commit()
        
    except Exception as e:
        session.rollback()
        result["errors"] += 1
        raise
    
    return result


def run(limit: int = 100) -> dict:
    """
    Main entry point for the background daemon.
    Processes axis scores in batches and updates server risk tiers.
    """
    results = {
        "total_processed": 0,
        "total_updated": 0,
        "total_errors": 0,
        "batches": 0
    }
    
    for _ in range(limit):
        with get_session() as session:
            batch_result = consume(session)
            results["batches"] += 1
            results["total_processed"] += batch_result["processed"]
            results["total_updated"] += batch_result["updated"]
            results["total_errors"] += batch_result["errors"]
            
            if batch_result["processed"] == 0:
                break
    
    return results


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    
    app = FastAPI()
    
    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "score_to_tier_consumer"}
    
    @app.post("/consume")
    def trigger_consume():
        with get_session() as session:
            result = consume(session)
        return result
    
    @app.get("/test")
    def test_logic():
        with get_session() as session:
            count = session.execute(
                select(func.count()).select_from(McpLlmAxisScore)
            ).scalar()
            registry_count = session.execute(
                select(func.count()).select_from(McpServerRegistry)
            ).scalar()
            
            if count is not None and registry_count is not None:
                return {"status": "PASS", "message": "Database connections verified"}
            else:
                return {"status": "FAIL", "message": "Database connection issue"}
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8773)