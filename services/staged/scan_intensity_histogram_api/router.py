from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_intensity_histogram

router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("/scan-intensity")
def scan_intensity_histogram(
    source: str | None = None,
    session: Session = Depends(get_session),
):
    return get_scan_intensity_histogram(
        session=session,
        registry_source=source,
    )