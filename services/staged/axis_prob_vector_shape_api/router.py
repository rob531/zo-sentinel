from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_prob_vector_shape

router = APIRouter(prefix="/api")


@router.get("/axis/prob-vector-shape")
def axis_prob_vector_shape(session: Session = Depends(get_session)):
    return get_axis_prob_vector_shape(session)