from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_model_versions

router = APIRouter(prefix="/api", tags=["scoring"])


@router.get("/scoring/axis-model-versions")
def axis_model_versions(session: Session = Depends(get_session)):
    return get_axis_model_versions(session)


if __name__ == "__main__":
    print("PASS")