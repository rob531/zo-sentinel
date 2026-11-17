from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

from pydantic import BaseModel

from app.db import get_session
from .logic import get_registry_meta_provenance

router = APIRouter(prefix="/api")


class ValueCount(BaseModel):
    value: str
    count: int


class KeyFrequency(BaseModel):
    key: str
    distinct: int
    values: List[ValueCount]


class RegistrySourceMix(BaseModel):
    registry_source: Optional[str]
    count: int


class Sample(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    last_seen: Optional[str]
    meta: Optional[Dict[str, Any]]


class RegistryMetaProvenanceResponse(BaseModel):
    with_meta: int
    without_meta: int
    total: int
    key_frequencies: List[KeyFrequency]
    registry_source_mix: List[RegistrySourceMix]
    samples: List[Sample]


@router.get(
    "/registry/meta-provenance",
    response_model=RegistryMetaProvenanceResponse,
    name="registry_meta_provenance",
)
def registry_meta_provenance(
    limit: int = 50,
    session: Session = Depends(get_session),
):
    """
    Return provenance information for registry metadata.
    """
    return get_registry_meta_provenance(session, limit)