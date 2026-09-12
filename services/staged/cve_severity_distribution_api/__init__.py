"""Service package initializer."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter()


class ServiceBase:
    """Base class providing a DB session for service implementations."""

    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    @classmethod
    def router(cls) -> APIRouter:
        """Return the shared router."""
        return router


@router.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Simple health endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    print("PASS")