"""
services/staged/axis_model_version_report/router.py
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from app.db import get_session
from .logic import get_model_versions_report

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class ModelVersionItem(BaseModel):
    model_version: str
    axis_count: int
    latest_scored_at: str


class ModelVersionsResponse(BaseModel):
    versions: List[ModelVersionItem]


@router.get("/model-versions", response_model=ModelVersionsResponse)
def get_model_versions(session=Depends(get_session)):
    return get_model_versions_report(session)