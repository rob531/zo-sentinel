from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def signal_scores_endpoint() -> Dict[str, List[Dict]]:
    """Fetches signal scores from the write service."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return {"signal_scores": response.json()}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/signal_scores")
async def get_signal_scores() -> Dict[str, List[Dict]]:
    """Endpoint to get signal scores."""
    return signal_scores_endpoint()

@app.get("/servers")
async def get_servers(db: Session = Depends(get_session)) -> List[Dict]:
    """Endpoint to get all servers from McpServerRegistry."""
    servers = db.query(McpServerRegistry).all()
    return [{"id": server.id, "name": server.name, "status": server.status} for server in servers]

@app.get("/scores/{server_id}")
async def get_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict]:
    """Endpoint to get scores for a specific server."""
    scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    return [{"id": score.id, "axis": score.axis, "value": score.value} for score in scores]

@app.post("/disputes")
async def create_dispute(dispute_data: Dict, db: Session = Depends(get_session)) -> Dict:
    """Endpoint to create a new score dispute."""
    dispute = McpScoreDispute(**dispute_data)
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return {"id": dispute.id, "status": "created"}

@app.get("/orgs")
async def get_orgs(db: Session = Depends(get_session)) -> List[Dict]:
    """Endpoint to get all organizations."""
    orgs = db.query(Org).all()
    return [{"id": org.id, "name": org.name} for org in orgs]

@app.get("/users/{org_id}")
async def get_users(org_id: int, db: Session = Depends(get_session)) -> List[Dict]:
    """Endpoint to get users for a specific organization."""
    users = db.query(User).filter(User.org_id == org_id).all()
    return [{"id": user.id, "name": user.name, "email": user.email} for user in users]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")