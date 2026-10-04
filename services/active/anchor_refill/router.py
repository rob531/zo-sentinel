from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpScoreDisputes, Orgs, Users
from sqlalchemy.orm import Session
import requests

router = APIRouter(prefix="/api", tags=["anchor_refill"])

@router.get("/anchor_refill")
async def anchor_refill(
    session: Session = Depends(get_session),
):
    try:
        # Fetch data from app tables
        server_registry = session.query(McpServerRegistry).all()
        llm_axis_scores = session.query(McpLlmAxisScores).all()
        score_disputes = session.query(McpScoreDisputes).all()
        orgs = session.query(Orgs).all()
        users = session.query(Users).all()

        # Fetch data from mesh/pipeline tables via write_service
        mesh_memory_response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mesh_memory", "params": []}
        )
        mesh_memory = mesh_memory_response.json()

        signal_scores_response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mcp_signal_scores", "params": []}
        )
        signal_scores = signal_scores_response.json()

        return {
            "server_registry": [item.__dict__ for item in server_registry],
            "llm_axis_scores": [item.__dict__ for item in llm_axis_scores],
            "score_disputes": [item.__dict__ for item in score_disputes],
            "orgs": [item.__dict__ for item in orgs],
            "users": [item.__dict__ for item in users],
            "mesh_memory": mesh_memory,
            "signal_scores": signal_scores,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(router, host="127.0.0.1", port=8000)