from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import ingest_cve_feed

router = APIRouter()


@router.post("/ingest/cve")
async def ingest_cve_endpoint(session: Session = Depends(get_session)):
    """
    Ingest the NVD CVE feed and store the results in the database.
    """
    return await ingest_cve_feed(session)