from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_user_role_matrix

router = APIRouter(prefix="/api")


@router.get("/orgs/users/roles")
def read_org_user_role_matrix(session: Session = Depends(get_session)):
    return get_org_user_role_matrix(session)


if __name__ == "__main__":
    print("PASS")