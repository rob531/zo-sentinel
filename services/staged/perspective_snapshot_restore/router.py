from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.dependencies import get_current_user
from app.models import User

from .logic import restore_perspective_snapshot

router = APIRouter(prefix="/api", tags=["perspective_snapshot_restore"])


@router.post(
    "/perspectives/{perspective_id}/restore",
    status_code=status.HTTP_200_OK,
)
def restore_snapshot(
    perspective_id: int,
    snapshot_id: int = Query(..., description="ID of the snapshot to restore"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Restore a perspective's membership to a previously taken snapshot.
    """
    return restore_perspective_snapshot(
        perspective_id=perspective_id,
        snapshot_id=snapshot_id,
        session=session,
        current_user=current_user,
    )