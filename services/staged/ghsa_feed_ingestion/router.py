from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import ingest_ghsa_feed, get_ingestion_status

router = APIRouter()


@router.post("/ingest")
def ingest_endpoint(db: Session = Depends(get_session)):
    """Trigger ingestion of the latest GHSA feed."""
    return ingest_ghsa_feed(db)


@router.get("/status")
def status_endpoint(db: Session = Depends(get_session)):
    """Retrieve the current ingestion status."""
    return get_ingestion_status(db)