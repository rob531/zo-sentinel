from __future__ import annotations

import csv
import io
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, and_

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["servers"])


class ServerExportFilters(BaseModel):
    risk_tier: Optional[str] = None
    registry_source: Optional[str] = None
    search: Optional[str] = None
    min_trust_score: Optional[float] = Field(None, ge=0, le=100)


def _build_where_clauses(filters: ServerExportFilters):
    clauses = []
    if filters.risk_tier:
        clauses.append(McpServerRegistry.risk_tier == filters.risk_tier)
    if filters.registry_source:
        clauses.append(McpServerRegistry.registry_source == filters.registry_source)
    if filters.search:
        search_term = f"%{filters.search}%"
        clauses.append(
            or_(
                McpServerRegistry.name.ilike(search_term),
                McpServerRegistry.description.ilike(search_term),
            )
        )
    if filters.min_trust_score is not None:
        clauses.append(McpServerRegistry.trust_score >= filters.min_trust_score)
    return clauses


CSV_COLUMNS = [
    "server_id",
    "name",
    "url",
    "registry_source",
    "trust_score",
    "verdict",
    "risk_tier",
    "last_assessed",
    "last_seen",
]


def _row_from_server(server: McpServerRegistry) -> dict:
    return {
        "server_id": server.server_id,
        "name": server.name,
        "url": server.url,
        "registry_source": server.registry_source,
        "trust_score": server.trust_score,
        "verdict": server.verdict,
        "risk_tier": server.risk_tier,
        "last_assessed": server.last_assessed.isoformat() if server.last_assessed else "",
        "last_seen": server.last_seen.isoformat() if server.last_seen else "",
    }


def _iter_rows(db, filters: ServerExportFilters):
    clauses = _build_where_clauses(filters)
    stmt = select(McpServerRegistry)
    if clauses:
        stmt = stmt.where(and_(*clauses))

    BATCH_SIZE = 100
    offset = 0

    while True:
        batch_stmt = stmt.offset(offset).limit(BATCH_SIZE)
        results = db.execute(batch_stmt).scalars().all()
        if not results:
            break

        for server in results:
            yield _row_from_server(server)

        offset += BATCH_SIZE


def stream_csv(db, filters: ServerExportFilters) -> AsyncIterator[bytes]:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    yield output.getvalue().encode("utf-8")

    for row in _iter_rows(db, filters):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writerow(row)
        yield output.getvalue().encode("utf-8")


@router.get(
    "/servers/export",
    response_class=StreamingResponse,
    summary="Export servers as CSV",
)
async def export_servers_csv(
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    registry_source: Optional[str] = Query(None, description="Filter by registry source"),
    search: Optional[str] = Query(None, description="Search name or description"),
    min_trust_score: Optional[float] = Query(None, ge=0, le=100, description="Minimum trust score"),
    db=Depends(get_session),
) -> StreamingResponse:
    filters = ServerExportFilters(
        risk_tier=risk_tier,
        registry_source=registry_source,
        search=search,
        min_trust_score=min_trust_score,
    )

    return StreamingResponse(
        stream_csv(db, filters),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=servers.csv"},
    )


def list_routers():
    return [router]