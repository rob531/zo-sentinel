from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import McpSignalScores, McpServerRegistry, McpLlmAxisScores, McpScoreDisputes, Orgs, Users
from sqlalchemy.orm import Session
import requests

router = APIRouter()

@router.get("/signal_scores_distribution")
async def get_signal_scores_distribution(
    session: Session = Depends(get_session),
    org_id: int = None,
    server_id: int = None
):
    """Get signal scores distribution for a specific organization or server."""
    query = session.query(McpSignalScores)
    
    if org_id:
        query = query.join(McpServerRegistry, McpSignalScores.server_id == McpServerRegistry.id)
        query = query.filter(McpServerRegistry.org_id == org_id)
    
    if server_id:
        query = query.filter(McpSignalScores.server_id == server_id)
    
    signal_scores = query.all()
    
    if not signal_scores:
        raise HTTPException(status_code=404, detail="No signal scores found")
    
    return {"signal_scores": [score.to_dict() for score in signal_scores]}

@router.get("/signal_scores_distribution/axis")
async def get_signal_scores_by_axis(
    session: Session = Depends(get_session),
    org_id: int = None,
    server_id: int = None
):
    """Get signal scores distribution by axis for a specific organization or server."""
    query = session.query(McpLlmAxisScores)
    
    if org_id:
        query = query.join(McpServerRegistry, McpLlmAxisScores.server_id == McpServerRegistry.id)
        query = query.filter(McpServerRegistry.org_id == org_id)
    
    if server_id:
        query = query.filter(McpLlmAxisScores.server_id == server_id)
    
    axis_scores = query.all()
    
    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found")
    
    return {"axis_scores": [score.to_dict() for score in axis_scores]}

@router.get("/signal_scores_distribution/disputes")
async def get_signal_score_disputes(
    session: Session = Depends(get_session),
    org_id: int = None,
    server_id: int = None
):
    """Get signal score disputes for a specific organization or server."""
    query = session.query(McpScoreDisputes)
    
    if org_id:
        query = query.join(McpServerRegistry, McpScoreDisputes.server_id == McpServerRegistry.id)
        query = query.filter(McpServerRegistry.org_id == org_id)
    
    if server_id:
        query = query.filter(McpScoreDisputes.server_id == server_id)
    
    disputes = query.all()
    
    if not disputes:
        raise HTTPException(status_code=404, detail="No disputes found")
    
    return {"disputes": [dispute.to_dict() for dispute in disputes]}

@router.get("/signal_scores_distribution/mesh")
async def get_mesh_signal_scores(
    org_id: int = None,
    server_id: int = None
):
    """Get mesh signal scores for a specific organization or server."""
    params = {}
    if org_id:
        params["org_id"] = org_id
    if server_id:
        params["server_id"] = server_id
    
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "sql": """
                SELECT * FROM mcp_signal_scores
                WHERE (:org_id IS NULL OR server_id IN (
                    SELECT id FROM mcp_server_registry WHERE org_id = :org_id
                ))
                AND (:server_id IS NULL OR server_id = :server_id)
            """,
            "params": params
        }
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh signal scores")
    
    return response.json()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(router, host="0.0.0.0", port=8000)