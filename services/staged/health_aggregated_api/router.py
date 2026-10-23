from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import *  # noqa: F403,F401 – expose logic symbols

router = APIRouter()


@router.get("/ping")
def ping(session: Session = Depends(get_session)):
    """Simple health‑check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    # Self‑test: ensure the module loads and the router object exists.
    try:
        _ = router
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print("FAIL", exc)
        raise