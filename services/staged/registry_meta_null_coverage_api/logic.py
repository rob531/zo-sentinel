from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class TopKeyCoverage(BaseModel):
    key: str
    count: int


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    with_meta: int
    without_meta: int
    top_keys: List[TopKeyCoverage]


class RegistryMetaCoverageResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    sources: List[RegistrySourceCoverage]


def _top_level_keys(meta: Any) -> Iterable[str]:
    """Return the top-level keys from a JSON object.

    SQLAlchemy normally materializes JSON/JSONB columns as dictionaries, but
    SQLite-backed test stores may return JSON as text. Supporting both forms
    keeps the computation portable without issuing database-specific JSON SQL.
    """
    if isinstance(meta, dict):
        return meta.keys()

    if isinstance(meta, (bytes, bytearray)):
        try:
            meta = meta.decode("utf-8")
        except UnicodeDecodeError:
            return ()

    if isinstance(meta, str):
        try:
            decoded = json.loads(meta)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        if isinstance(decoded, dict):
            return decoded.keys()
        return ()

    return ()


def get_registry_meta_coverage(
    session: Session,
    limit: int = 100,
) -> RegistryMetaCoverageResponse:
    """Compute registry metadata coverage from the application database.

    Only the requested columns from ``mcp_server_registry`` are read. The
    ``limit`` controls the maximum number of registry sources included in the
    response; coverage for each included source is calculated across all of its
    rows.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.meta,
        ).order_by(
            McpServerRegistry.registry_source.asc(),
            McpServerRegistry.server_id.asc(),
        )
    ).all()

    source_totals: Counter[str] = Counter()
    source_with_meta: Counter[str] = Counter()
    source_without_meta: Counter[str] = Counter()
    source_keys: Dict[str, Counter[str]] = {}

    for row in rows:
        registry_source = str(row.registry_source)
        source_totals[registry_source] += 1

        if row.meta is None:
            source_without_meta[registry_source] += 1
        else:
            source_with_meta[registry_source] += 1
            keys = source_keys.setdefault(registry_source, Counter())
            keys.update(_top_level_keys(row.meta))

    ordered_sources = sorted(source_totals)[:limit]
    sources: List[RegistrySourceCoverage] = []

    for registry_source in ordered_sources:
        key_counts = source_keys.get(registry_source, Counter())
        top_keys = [
            TopKeyCoverage(key=key, count=count)
            for key, count in sorted(
                key_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        sources.append(
            RegistrySourceCoverage(
                registry_source=registry_source,
                total=source_totals[registry_source],
                with_meta=source_with_meta[registry_source],
                without_meta=source_without_meta[registry_source],
                top_keys=top_keys,
            )
        )

    return RegistryMetaCoverageResponse(
        generated_at=datetime.now(timezone.utc),
        total_servers=sum(source.total for source in sources),
        sources=sources,
    )


compute_registry_meta_coverage = get_registry_meta_coverage
get_meta_coverage = get_registry_meta_coverage

router = APIRouter(prefix="/api")
api_router = router
registry_meta_null_coverage_router = router


@router.get(
    "/registry/meta-coverage",
    response_model=RegistryMetaCoverageResponse,
)
def registry_meta_coverage(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> RegistryMetaCoverageResponse:
    return get_registry_meta_coverage(session=session, limit=limit)


app = FastAPI(
    title="Registry Metadata Null Coverage API",
    version="1.0.0",
)
app.include_router(router)


if __name__ == "__main__":
    keys = list(_top_level_keys({"scan_depth": 3, "licence": "MIT"}))
    assert sorted(keys) == ["licence", "scan_depth"]
    assert list(_top_level_keys("{}")) == []
    assert list(_top_level_keys(None)) == []
    assert list(_top_level_keys('[1, 2, 3]')) == []
    print("PASS")