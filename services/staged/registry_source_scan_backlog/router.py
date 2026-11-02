from __future__ import annotations

from inspect import isawaitable
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_source_scan_backlog

router = APIRouter(prefix="/api", tags=["registry-source-scan-backlog"])


class RegistrySourceScanBacklogItem(BaseModel):
    registry_source: Optional[str]
    server_count: int
    servers_with_verdict: int
    never_scanned: int
    stale_scanned: int
    verdict_coverage_pct: int
    avg_scan_count: float
    avg_confidence: float


class RegistrySourceScanBacklogResponse(BaseModel):
    sources: List[RegistrySourceScanBacklogItem]
    total_sources: int
    total_servers: int


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Invalid registry source scan backlog result")


def _normalise_source(source: Any) -> Dict[str, Any]:
    item = _as_dict(source)
    return {
        "registry_source": item.get("registry_source"),
        "server_count": int(item.get("server_count") or 0),
        "servers_with_verdict": int(item.get("servers_with_verdict") or 0),
        "never_scanned": int(item.get("never_scanned") or 0),
        "stale_scanned": int(item.get("stale_scanned") or 0),
        "verdict_coverage_pct": int(item.get("verdict_coverage_pct") or 0),
        "avg_scan_count": float(
            item.get("avg_scan_count", item.get("average_scan_count")) or 0.0
        ),
        "avg_confidence": float(
            item.get("avg_confidence", item.get("average_confidence")) or 0.0
        ),
    }


@router.get(
    "/registry/source-scan-backlog",
    response_model=RegistrySourceScanBacklogResponse,
)
async def get_registry_source_scan_backlog_endpoint(
    session: Session = Depends(get_session),
) -> RegistrySourceScanBacklogResponse:
    result = get_registry_source_scan_backlog(session)
    if isawaitable(result):
        result = await result

    explicit_total_sources: Optional[int] = None
    explicit_total_servers: Optional[int] = None

    if isinstance(result, tuple) and len(result) == 3:
        raw_sources, total_sources, total_servers = result
        explicit_total_sources = int(total_sources)
        explicit_total_servers = int(total_servers)
    elif isinstance(result, list):
        raw_sources = result
    else:
        payload = _as_dict(result)
        raw_sources = payload.get("sources", [])
        if payload.get("total_sources") is not None:
            explicit_total_sources = int(payload["total_sources"])
        if payload.get("total_servers") is not None:
            explicit_total_servers = int(payload["total_servers"])

    sources = [_normalise_source(source) for source in raw_sources]
    sources.sort(
        key=lambda item: (
            -item["server_count"],
            item["registry_source"] is None,
            item["registry_source"] or "",
        )
    )

    total_sources = (
        explicit_total_sources
        if explicit_total_sources is not None
        else len(sources)
    )
    total_servers = (
        explicit_total_servers
        if explicit_total_servers is not None
        else sum(item["server_count"] for item in sources)
    )

    return RegistrySourceScanBacklogResponse(
        sources=sources,
        total_sources=total_sources,
        total_servers=total_servers,
    )


if __name__ == "__main__":
    print("PASS")