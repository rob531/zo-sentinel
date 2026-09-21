from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import *  # import all public symbols from the service's logic module

router = APIRouter()


@router.get("/health")
def health_endpoint(db: Session = Depends(get_session)):
    """
    Health check endpoint.
    If the logic module defines a `health` callable, delegate to it;
    otherwise return a minimal OK payload.
    """
    if "health" in globals() and callable(health):  # type: ignore
        return health(db)  # pragma: no cover
    return {"status": "ok"}


@router.post("/process")
def process_endpoint(db: Session = Depends(get_session)):
    """
    Trigger the risk‑tier consumer processing.
    If the logic module defines a `process` callable, invoke it;
    otherwise respond with a no‑op acknowledgement.
    """
    if "process" in globals() and callable(process):  # type: ignore
        process(db)  # pragma: no cover
        return {"status": "processed"}
    return {"status": "no_process"}


__all__ = ["router"]