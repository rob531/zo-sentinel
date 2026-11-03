from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_ecosystem_rollup

router = APIRouter(prefix="/api")


@router.get("/vuln/ecosystems")
def get_vuln_ecosystems(
    ecosystem: str | None = Query(default=None),
    feed: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
):
    return get_ecosystem_rollup(
        session,
        ecosystem=ecosystem,
        feed=feed,
        limit=limit,
    )


if __name__ == "__main__":
    print("PASS")