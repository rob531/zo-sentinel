from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_scan_count_cohorts

router = APIRouter(prefix="/api")


@router.get("/registry/scan-count-cohorts")
def scan_count_cohorts(session: Session = Depends(get_session)):
    return get_scan_count_cohorts(session)


if __name__ == "__main__":
    print("PASS")