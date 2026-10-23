# services/staged/app_scoring_consumer_network_egress/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import compute_score, list_servers_to_score

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/score/{server_id}")
def score_server(server_id: str, db: Session = Depends(get_session)):
    result = compute_score(server_id)
    return result


@router.post("/score_all")
def score_all(db: Session = Depends(get_session)):
    servers = list_servers_to_score()
    results = []
    for server_id in servers:
        result = compute_score(server_id)
        results.append({"server_id": server_id, **result})
    return results


@router.get("/servers_to_score")
def get_servers_to_score(db: Session = Depends(get_session)):
    return list_servers_to_score()