from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import mcp_server_registry_source_distribution

router = APIRouter(prefix="/api", tags=["mcp_server_registry_source_distribution_dashboard"])


@router.get("/mcp/server-registry/source-distribution")
def get_source_distribution(session: Session = Depends(get_session)):
    return mcp_server_registry_source_distribution(session)