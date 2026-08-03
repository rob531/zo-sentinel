from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import (
    get_perspectives,
    create_perspective,
    get_perspective,
    perspective_snapshot_insert,
)
from .contract import (
    PerspectiveCreate,
    PerspectiveResponse,
    SnapshotCreate,
    SnapshotResponse,
)

router = APIRouter(prefix="/perspectives", tags=["perspectives"])


@router.get("/", response_model=List[PerspectiveResponse])
def list_perspectives(
    org_id: str,
    db: Session = Depends(get_session),
):
    """Return all perspectives belonging to an organisation."""
    return get_perspectives(db=db, org_id=org_id)


@router.post(
    "/",
    response_model=PerspectiveResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_perspective_endpoint(
    payload: PerspectiveCreate,
    db: Session = Depends(get_session),
):
    """Create a new perspective."""
    return create_perspective(db=db, payload=payload)


@router.get("/{perspective_id}", response_model=PerspectiveResponse)
def get_perspective_endpoint(
    perspective_id: str,
    db: Session = Depends(get_session),
):
    """Retrieve a single perspective by its identifier."""
    perspective = get_perspective(db=db, perspective_id=perspective_id)
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return perspective


@router.post(
    "/{perspective_id}/snapshot",
    response_model=SnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def take_snapshot_endpoint(
    perspective_id: str,
    payload: SnapshotCreate,
    db: Session = Depends(get_session),
):
    """Take a snapshot of a perspective."""
    return perspective_snapshot_insert(
        db=db,
        perspective_id=perspective_id,
        org_id=payload.org_id,
        membership=payload.membership,
    )