from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_role_distribution

router = APIRouter(prefix="/api", tags=["user-role-distribution"])


@router.get("/users/role-distribution")
def read_user_role_distribution(session: Session = Depends(get_session)):
    return get_role_distribution(session)