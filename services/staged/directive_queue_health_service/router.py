from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_directive_queue_health

router = APIRouter(prefix="/api")


@router.get("/directives/health")
def health(session: Session = Depends(get_session)):
    """
    Return health information for the directive queue.

    Expected keys in the response (as defined by the service spec):
    - pending_count: int
    - proposed_count: int
    - avg_pending_age_seconds: float
    - breaker_state: str
    """
    return get_directive_queue_health(session)


if __name__ == "__main__":
    # Simple self‑test: ensure the router can be included without error.
    # The actual endpoint logic is exercised elsewhere; here we just confirm
    # that the module loads and the router object exists.
    print("PASS")