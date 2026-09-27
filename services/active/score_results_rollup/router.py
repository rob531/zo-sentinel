
from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores, Session
import requests

router = APIRouter(
    prefix="/api",
    tags=["score_results_rollup"],
    responses={404: {"description": "Not found"}},
)

@router.get("/scores/rollup", response_model=dict)

async def get_score_rollup(
    server_id: str = Query(..., min_length=36, max_length=36, regex=r'^[0-9a-f-]+$'),
    session: Session = Depends(get_session)
):
    # Implementation will go here
    
    # Get server info from APP tables
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get axis scores from APP tables
    axis_scores = session.query(MCPAxisScores).filter(MCPAxisScores.server_id == server_id).all()

    # Get signal scores from MESH tables via write_service
    response = requests.post(
        'http://127.0.0.1:8772/query',
        json={
            'sql': 'SELECT * FROM mcp_signal_scores WHERE server_id = :server_id',
            'params': {'server_id': server_id}
        }
    )
    signal_scores = response.json()['rows']

    # Basic rollup logic
    rollup = {
        'server_id': server_id,
        'server_name': server.name,
        'axis_scores': {score.axis: score.value for score in axis_scores},
        'signal_scores': signal_scores,
        'overall_score': calculate_overall_score(axis_scores, signal_scores)
    }

    return rollup

