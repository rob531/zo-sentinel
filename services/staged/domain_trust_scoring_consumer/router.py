from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_domain_trust_score, run

router = APIRouter()


@router.get("/score")
def score_endpoint(
    server_id: str,
    url: str,
    name: str,
    db: Session = Depends(get_session),
):
    """Compute a domain‑trust score for a single server."""
    score = compute_domain_trust_score(server_id, url, name)
    return {"server_id": server_id, "score": score}


@router.post("/run")
def run_endpoint(db: Session = Depends(get_session)):
    """Run the full domain‑trust scoring job."""
    results = run()
    return {"results": results}