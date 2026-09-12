from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot
from .logic import (
    create_perspective,
    update_perspective,
    delete_perspective,
    get_perspective,
    list_perspectives,
    take_snapshot,
    get_latest_snapshot
)

router = APIRouter()

@router.post("/perspectives")
async def create_perspective_endpoint(
    org_id: int,
    name: str,
    description: str,
    facet_filters: dict,
    created_by: int,
    session: Session = Depends(get_session)
):
    return create_perspective(session, org_id, name, description, facet_filters, created_by)

@router.put("/perspectives/{perspective_id}")
async def update_perspective_endpoint(
    perspective_id: int,
    updates: dict,
    session: Session = Depends(get_session)
):
    return update_perspective(session, perspective_id, updates)

@router.delete("/perspectives/{perspective_id}")
async def delete_perspective_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    return delete_perspective(session, perspective_id)

@router.get("/perspectives/{perspective_id}")
async def get_perspective_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    return get_perspective(session, perspective_id)

@router.get("/perspectives")
async def list_perspectives_endpoint(
    org_id: int,
    session: Session = Depends(get_session)
):
    return list_perspectives(session, org_id)

@router.post("/perspectives/{perspective_id}/snapshots")
async def take_snapshot_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    return take_snapshot(session, perspective_id)

@router.get("/perspectives/{perspective_id}/snapshots/latest")
async def get_latest_snapshot_endpoint(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    return get_latest_snapshot(session, perspective_id)