from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

try:
    from .logic import lookup_vuln_advisories as _lookup_vuln_advisories
except ImportError:
    try:
        from .logic import lookup_advisories as _lookup_vuln_advisories
    except ImportError:
        try:
            from .logic import get_advisories as _lookup_vuln_advisories
        except ImportError:
            from .logic import query_advisories as _lookup_vuln_advisories


router = APIRouter(prefix="/api", tags=["vuln-advisories"])


class AdvisoryServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    match_basis: Optional[str] = None


class Advisory(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: str
    published_at: Optional[datetime] = None
    match_confidence: Optional[float] = None
    servers: List[AdvisoryServer] = Field(default_factory=list)


class AdvisoryLookupResponse(BaseModel):
    total: int
    advisories: List[Advisory]


_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}


@router.get(
    "/vulns/advisories",
    response_model=AdvisoryLookupResponse,
    summary="Look up known vulnerabilities affecting MCP servers",
)
def list_vuln_advisories(
    severity: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    server_id: Optional[str] = Query(default=None),
    limit: str = Query(default="50"),
    session=Depends(get_session),
) -> AdvisoryLookupResponse:
    if severity is not None and severity.lower() not in _ALLOWED_SEVERITIES:
        raise HTTPException(status_code=400, detail="Unknown severity value")

    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit must be an integer")

    if parsed_limit < 1 or parsed_limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")

    result = _lookup_vuln_advisories(
        session=session,
        severity=severity,
        ecosystem=ecosystem,
        server_id=server_id,
        limit=parsed_limit,
    )

    if isinstance(result, AdvisoryLookupResponse):
        return result

    return AdvisoryLookupResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    client = TestClient(test_app)
    response = client.get("/api/vulns/advisories?severity=not-a-severity")
    assert response.status_code == 400
    print("PASS")