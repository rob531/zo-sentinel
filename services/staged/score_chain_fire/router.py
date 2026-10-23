# services/staged/score_chain_fire/router.py
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring"])


class FireScoreResponse(BaseModel):
    fired: int
    server_ids: List[str]
    queued_at: str


# In-memory queue for scoring requests (module-level for simplicity)
_scoring_queue: List[dict] = []


def enqueue_scoring_request(server_id: str) -> None:
    """Add a server to the scoring queue."""
    _scoring_queue.append({
        "server_id": server_id,
        "queued_at": datetime.now(timezone.utc).isoformat()
    })


def get_queued_servers() -> List[dict]:
    """Return current queue state (for testing/debugging)."""
    return list(_scoring_queue)


def clear_queue() -> None:
    """Clear the scoring queue (for testing)."""
    _scoring_queue.clear()


def fire_score_logic(db: Session) -> dict:
    """
    Query mcp_server_registry for servers needing rescoring.
    Selects servers where last_scanned IS NULL or scan_count=0.
    Enqueues scoring requests and returns results.
    """
    # Query for servers needing scoring
    query = text("""
        SELECT server_id
        FROM mcp_server_registry
        WHERE last_scanned IS NULL
           OR scan_count = 0
    """)
    
    result = db.execute(query)
    server_ids = [row[0] for row in result.fetchall()]
    
    # Enqueue scoring requests
    for server_id in server_ids:
        enqueue_scoring_request(server_id)
    
    queued_at = datetime.now(timezone.utc).isoformat()
    
    return {
        "fired": len(server_ids),
        "server_ids": server_ids,
        "queued_at": queued_at
    }


@router.post("/scoring/fire", response_model=FireScoreResponse)
def fire_score(session: Session = Depends(get_session)) -> FireScoreResponse:
    """
    Fire scoring requests for all servers that need initial scoring.
    This is a trigger endpoint - it enqueues work for the scoring consumer.
    Returns the count and IDs of servers queued for scoring.
    """
    result = fire_score_logic(session)
    return FireScoreResponse(**result)