from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_feed_health

router = APIRouter(prefix="/api", tags=["vuln-advisories"])


@router.get("/vuln/advisories/feed-health")
def vuln_advisory_feed_health(
    session: Session = Depends(get_session),
) -> dict:
    return get_feed_health(session)


if __name__ == "__main__":
    print("PASS")