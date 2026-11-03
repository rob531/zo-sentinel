from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_drilldown

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/axis-drilldown")
def axis_drilldown_endpoint(
    server_id: str,
    session: Session = Depends(get_session),
):
    return get_axis_drilldown(server_id, session)