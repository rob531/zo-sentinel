# services/_exemplar/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])

@router.get("/servers")
def list_servers(session: Session = Depends(get_session)):
    ...