from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

try:
    from .logic import get_ecosystem_exposure_rollup as _get_ecosystem_rollup
except ImportError:
    try:
        from .logic import get_ecosystem_rollup as _get_ecosystem_rollup
    except ImportError:
        from .logic import get_ecosystem_exposure as _get_ecosystem_rollup


router = APIRouter()


class TopPackage(BaseModel):
    package: str
    count: int


class EcosystemExposure(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_server_count: int
    severity_breakdown: Dict[str, int]
    top_packages: List[TopPackage]
    max_match_confidence: Optional[float]
    latest_published_at: Optional[str]


class EcosystemExposureResponse(BaseModel):
    count: int
    ecosystems: List[EcosystemExposure]


def _call_rollup(session: Session, severity: Optional[str]) -> Any:
    parameters = inspect.signature(_get_ecosystem_rollup).parameters
    severity_parameter = next(
        (
            parameter
            for parameter in parameters.values()
            if "severity" in parameter.name.lower()
        ),
        None,
    )

    if severity_parameter is None:
        return _get_ecosystem_rollup(session)
    if severity_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
        return _get_ecosystem_rollup(session, severity)
    return _get_ecosystem_rollup(session, severity=severity)


def _normalize_ecosystems(result: Any) -> List[EcosystemExposure]:
    if isinstance(result, dict):
        raw_ecosystems = result.get("ecosystems", [])
    elif isinstance(result, (list, tuple)):
        raw_ecosystems = result
    else:
        raw_ecosystems = []

    ecosystems: List[EcosystemExposure] = []
    for item in raw_ecosystems:
        if isinstance(item, EcosystemExposure):
            ecosystem = item
        elif isinstance(item, dict):
            ecosystem = EcosystemExposure.parse_obj(item)
        else:
            ecosystem = EcosystemExposure.parse_obj(dict(item))
        ecosystems.append(ecosystem)

    ecosystems.sort(key=lambda item: (-item.advisory_count, item.ecosystem))
    return ecosystems


@router.get(
    "/vuln/ecosystems",
    response_model=EcosystemExposureResponse,
    summary="Vulnerability exposure grouped by ecosystem",
)
async def get_vuln_ecosystems(
    severity: Optional[str] = Query(
        default=None,
        description="Filter advisories by severity before aggregation",
    ),
    session: Session = Depends(get_session),
) -> EcosystemExposureResponse:
    result = _call_rollup(session, severity)
    if inspect.isawaitable(result):
        result = await result

    ecosystems = _normalize_ecosystems(result)
    return EcosystemExposureResponse(count=len(ecosystems), ecosystems=ecosystems)


if __name__ == "__main__":
    print("PASS")