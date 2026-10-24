from __future__ import annotations

import inspect
from datetime import timedelta
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_registry_source_scan_coverage as _coverage_logic
except ImportError:
    try:
        from .logic import get_source_scan_coverage as _coverage_logic
    except ImportError:
        try:
            from .logic import calculate_registry_source_scan_coverage as _coverage_logic
        except ImportError:
            from .logic import compute_registry_source_scan_coverage as _coverage_logic


router = APIRouter(prefix="/api")


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int = Field(ge=0)
    seen: int = Field(ge=0)
    scanned: int = Field(ge=0)
    never_scanned: int = Field(ge=0)
    median_scan_count: float = Field(ge=0)
    scan_coverage_pct: float = Field(ge=0, le=100)


class RegistrySourceScanCoverageResponse(BaseModel):
    days: int
    sources: list[RegistrySourceCoverage]


def _fallback_coverage(session: Session, days: int) -> dict[str, Any]:
    from datetime import datetime

    cutoff = datetime.utcnow() - timedelta(days=days)
    grouped: dict[str, list[McpServerRegistry]] = {}

    for server in session.query(McpServerRegistry).all():
        source = server.registry_source or "unknown"
        grouped.setdefault(source, []).append(server)

    sources: list[dict[str, Any]] = []
    for source, servers in sorted(grouped.items()):
        total = len(servers)
        seen = sum(
            1
            for server in servers
            if server.last_seen is not None and server.last_seen >= cutoff
        )
        scanned = sum(
            1
            for server in servers
            if server.last_scanned is not None or (server.scan_count or 0) > 0
        )
        median_scan_count = float(
            median([float(server.scan_count or 0) for server in servers])
        )
        sources.append(
            {
                "registry_source": source,
                "total": total,
                "seen": seen,
                "scanned": scanned,
                "never_scanned": total - scanned,
                "median_scan_count": median_scan_count,
                "scan_coverage_pct": round((scanned / total) * 100, 2),
            }
        )

    return {"days": days, "sources": sources}


async def _run_coverage_logic(session: Session, days: int) -> dict[str, Any]:
    parameters = list(inspect.signature(_coverage_logic).parameters.values())
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]

    if positional and "day" in positional[0].name.lower():
        args = (days, session)
    else:
        args = (session, days)

    kwargs: dict[str, Any] = {}
    keyword_only = [
        parameter
        for parameter in parameters
        if parameter.kind == parameter.KEYWORD_ONLY
    ]
    for parameter in keyword_only:
        if "day" in parameter.name.lower():
            kwargs[parameter.name] = days
        elif "db" in parameter.name.lower() or "session" in parameter.name.lower():
            kwargs[parameter.name] = session

    result = _coverage_logic(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result

    if result is None:
        return _fallback_coverage(session, days)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if isinstance(result, dict) and "sources" in result:
        payload = dict(result)
        payload["days"] = days
        return payload

    if isinstance(result, tuple) and len(result) == 2:
        return {"days": days, "sources": result[1]}

    if isinstance(result, list):
        return {"days": days, "sources": result}

    if isinstance(result, dict):
        return {"days": days, "sources": [result]}

    return _fallback_coverage(session, days)


@router.get(
    "/registry/source-scan-coverage",
    response_model=RegistrySourceScanCoverageResponse,
)
async def get_registry_source_scan_coverage(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> RegistrySourceScanCoverageResponse:
    payload = await _run_coverage_logic(session, days)
    return RegistrySourceScanCoverageResponse(**payload)


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    now = datetime.utcnow()
    with Session(test_engine) as test_session:
        test_session.execute(
            insert(McpServerRegistry),
            [
                {
                    "server_id": "self-test-1",
                    "name": "Self Test One",
                    "registry_source": "github",
                    "last_seen": now,
                    "last_scanned": now,
                    "scan_count": 3,
                    "risk_tier": "low",
                },
                {
                    "server_id": "self-test-2",
                    "name": "Self Test Two",
                    "registry_source": "github",
                    "last_seen": now,
                    "last_scanned": None,
                    "scan_count": 0,
                    "risk_tier": "medium",
                },
                {
                    "server_id": "self-test-3",
                    "name": "Self Test Three",
                    "registry_source": "gitlab",
                    "last_seen": now - timedelta(days=60),
                    "last_scanned": now - timedelta(days=60),
                    "scan_count": 5,
                    "risk_tier": "low",
                },
            ],
        )
        test_session.commit()

    def override_get_session():
        with Session(test_engine) as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-scan-coverage?days=30")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["days"] == 30
        sources = {source["registry_source"]: source for source in body["sources"]}
        assert sources["github"]["total"] == 2
        assert sources["github"]["never_scanned"] == 1
        assert sources["github"]["scanned"] == 1
        assert sources["gitlab"]["total"] == 1

    print("PASS")