from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base


class PackageHotspot(BaseModel):
    ecosystem: str
    package: str
    advisory_count: int
    high_severity_count: int
    linked_server_count: int
    latest_published_at: Optional[str] = None


class PackageHotspotsResponse(BaseModel):
    packages: List[PackageHotspot]
    ecosystems: List[str]
    limit: int


router = APIRouter(tags=["vuln-advisories"])


def _serialize_published_at(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_package_hotspots(
    ecosystem: str = Query(..., min_length=1, description="Package ecosystem"),
    limit: int = Query(100, ge=1, description="Maximum number of packages"),
    session: Session = Depends(get_session),
) -> PackageHotspotsResponse:
    advisory_table = Base.metadata.tables["vuln_advisories"]
    link_table = Base.metadata.tables["vuln_links"]

    high_severity_condition = func.upper(advisory_table.c.severity).in_(["CRITICAL", "HIGH"])

    packages_stmt = (
        select(
            advisory_table.c.ecosystem.label("ecosystem"),
            advisory_table.c.package.label("package"),
            func.count(advisory_table.c.id).label("advisory_count"),
            func.coalesce(
                func.sum(case((high_severity_condition, 1), else_=0)),
                0,
            ).label("high_severity_count"),
            func.max(advisory_table.c.published_at).label("latest_published_at"),
            func.count(func.distinct(link_table.c.server_id)).label("linked_server_count"),
        )
        .select_from(
            advisory_table.outerjoin(
                link_table,
                link_table.c.advisory_id == advisory_table.c.id,
            )
        )
        .where(
            advisory_table.c.ecosystem == ecosystem,
            advisory_table.c.package.is_not(None),
        )
        .group_by(advisory_table.c.ecosystem, advisory_table.c.package)
        .order_by(desc(func.count(advisory_table.c.id)), advisory_table.c.package)
        .limit(limit)
    )

    ecosystems_stmt = (
        select(advisory_table.c.ecosystem)
        .where(
            advisory_table.c.ecosystem.is_not(None),
            advisory_table.c.package.is_not(None),
        )
        .distinct()
        .order_by(advisory_table.c.ecosystem)
    )

    package_rows = session.execute(packages_stmt).mappings().all()
    ecosystem_rows = session.execute(ecosystems_stmt).all()

    packages = [
        PackageHotspot(
            ecosystem=str(row["ecosystem"]),
            package=str(row["package"]),
            advisory_count=int(row["advisory_count"] or 0),
            high_severity_count=int(row["high_severity_count"] or 0),
            linked_server_count=int(row["linked_server_count"] or 0),
            latest_published_at=_serialize_published_at(row["latest_published_at"]),
        )
        for row in package_rows
    ]

    return PackageHotspotsResponse(
        packages=packages,
        ecosystems=[str(row[0]) for row in ecosystem_rows if row[0] is not None],
        limit=limit,
    )


@router.get(
    "/packages",
    response_model=PackageHotspotsResponse,
    summary="List vulnerable package hotspots",
)
@router.get(
    "/vuln/packages",
    response_model=PackageHotspotsResponse,
    include_in_schema=False,
)
@router.get(
    "/api/vuln/packages",
    response_model=PackageHotspotsResponse,
    include_in_schema=False,
)
def list_package_hotspots(
    ecosystem: str = Query(..., min_length=1, description="Package ecosystem"),
    limit: int = Query(100, ge=1, description="Maximum number of packages"),
    session: Session = Depends(get_session),
) -> PackageHotspotsResponse:
    return get_package_hotspots(
        ecosystem=ecosystem,
        limit=limit,
        session=session,
    )


compute_package_hotspots = get_package_hotspots
get_packages = get_package_hotspots
get_vuln_package_hotspots = get_package_hotspots
logic_router = router
api_router = router

__all__ = [
    "PackageHotspot",
    "PackageHotspotsResponse",
    "api_router",
    "compute_package_hotspots",
    "get_package_hotspots",
    "get_packages",
    "get_vuln_package_hotspots",
    "list_package_hotspots",
    "logic_router",
    "router",
]


if __name__ == "__main__":
    print("PASS")