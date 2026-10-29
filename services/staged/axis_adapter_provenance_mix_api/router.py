from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_adapter_provenance_mix as _get_adapter_provenance_mix


ALLOWED_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AdapterProvenanceMixEntry(BaseModel):
    adapter_sha256: str
    model_version: str
    rows: int
    servers: int
    share: float


class AdapterProvenanceMixResponse(BaseModel):
    total_rows: int
    by_adapter: List[AdapterProvenanceMixEntry]
    axes_covered: List[str]


router = APIRouter()


@router.get(
    "/axis/adapter-provenance-mix",
    response_model=AdapterProvenanceMixResponse,
    summary="Report the distribution of axis scores by adapter and model version",
)
def adapter_provenance_mix(
    axis: Optional[str] = Query(
        None,
        description="Optional axis name to filter by",
    ),
    days: int = Query(
        30,
        description="Number of days of scored_at history to include",
    ),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if axis is not None and axis not in ALLOWED_AXES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unknown axis",
                "allowed_axes": ALLOWED_AXES,
            },
        )

    result = _get_adapter_provenance_mix(
        session=session,
        axis=axis,
        days=days,
    )

    if isinstance(result, AdapterProvenanceMixResponse):
        return {
            "total_rows": result.total_rows,
            "by_adapter": [entry.dict() for entry in result.by_adapter],
            "axes_covered": result.axes_covered,
        }

    return result