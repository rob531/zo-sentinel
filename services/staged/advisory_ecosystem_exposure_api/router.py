from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(tags=["vulnerability-advisory-exposure"])

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "moderate": 2,
    "low": 3,
    "info": 4,
    "informational": 4,
    "none": 5,
    "unknown": 6,
}
HIGH_SEVERITIES = ("critical", "high")


class EcosystemExposure(BaseModel):
    ecosystem: str
    advisory_count: int
    link_count: int
    max_severity: str
    high_severity_links: int


class SeverityExposure(BaseModel):
    severity: str
    advisory_count: int
    link_count: int


class ExposedServer(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    link_count: int
    max_match_confidence: float


class EcosystemExposureResponse(BaseModel):
    total_links: int
    by_ecosystem: List[EcosystemExposure]
    by_severity: List[SeverityExposure]
    top_exposed_servers: List[ExposedServer]


def _severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity, len(SEVERITY_ORDER))


@router.get(
    "/api/vulns/ecosystem-exposure",
    response_model=EcosystemExposureResponse,
)
def get_ecosystem_exposure(
    severity: Optional[str] = Query(None),
    ecosystem: Optional[str] = Query(None),
    limit: int = Query(100, ge=1),
    session: Session = Depends(get_session),
) -> EcosystemExposureResponse:
    severity_filter = severity.strip().lower() if severity else None
    ecosystem_filter = ecosystem.strip().lower() if ecosystem else None

    advisory_query = select(
        VulnAdvisory.id.label("advisory_id"),
        VulnAdvisory.ecosystem.label("ecosystem"),
        VulnAdvisory.severity.label("severity"),
    )
    if severity_filter is not None:
        advisory_query = advisory_query.where(
            func.lower(VulnAdvisory.severity) == severity_filter
        )
    if ecosystem_filter is not None:
        advisory_query = advisory_query.where(
            func.lower(VulnAdvisory.ecosystem) == ecosystem_filter
        )

    selected_advisories = advisory_query.order_by(
        VulnAdvisory.published_at.desc().nullslast(),
        VulnAdvisory.id,
    ).limit(limit).subquery("selected_advisories")

    ecosystem_severity_query = (
        select(
            selected_advisories.c.ecosystem.label("ecosystem"),
            selected_advisories.c.severity.label("severity"),
            func.count(distinct(selected_advisories.c.advisory_id)).label(
                "advisory_count"
            ),
            func.count(VulnLink.id).label("link_count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            func.lower(selected_advisories.c.severity).in_(
                                HIGH_SEVERITIES
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("high_severity_links"),
        )
        .select_from(
            selected_advisories.outerjoin(
                VulnLink,
                VulnLink.advisory_id == selected_advisories.c.advisory_id,
            )
        )
        .group_by(
            selected_advisories.c.ecosystem,
            selected_advisories.c.severity,
        )
    )

    registry_context = (
        select(
            McpServerRegistry.server_id.label("server_id"),
            func.max(McpServerRegistry.name).label("name"),
            func.max(McpServerRegistry.risk_tier).label("risk_tier"),
        )
        .group_by(McpServerRegistry.server_id)
        .subquery("registry_context")
    )

    server_query = (
        select(
            VulnLink.server_id.label("server_id"),
            func.max(registry_context.c.name).label("name"),
            func.max(registry_context.c.risk_tier).label("risk_tier"),
            func.count(VulnLink.id).label("link_count"),
            func.max(VulnLink.match_confidence).label("max_match_confidence"),
        )
        .select_from(
            selected_advisories.join(
                VulnLink,
                VulnLink.advisory_id == selected_advisories.c.advisory_id,
            ).outerjoin(
                registry_context,
                registry_context.c.server_id == VulnLink.server_id,
            )
        )
        .group_by(VulnLink.server_id)
    )

    ecosystem_severity_rows = session.execute(
        ecosystem_severity_query
    ).mappings()
    server_rows = session.execute(server_query).mappings()

    ecosystem_groups: dict[str, dict] = {}
    severity_groups: dict[str, dict] = {}

    for row in ecosystem_severity_rows:
        ecosystem_name = (
            str(row["ecosystem"]).strip().lower()
            if row["ecosystem"] is not None
            else "unknown"
        )
        severity_name = (
            str(row["severity"]).strip().lower()
            if row["severity"] is not None
            else "unknown"
        )
        advisory_count = int(row["advisory_count"] or 0)
        link_count = int(row["link_count"] or 0)
        high_severity_links = int(row["high_severity_links"] or 0)

        ecosystem_group = ecosystem_groups.setdefault(
            ecosystem_name,
            {
                "advisory_count": 0,
                "link_count": 0,
                "high_severity_links": 0,
                "severities": set(),
            },
        )
        ecosystem_group["advisory_count"] += advisory_count
        ecosystem_group["link_count"] += link_count
        ecosystem_group["high_severity_links"] += high_severity_links
        ecosystem_group["severities"].add(severity_name)

        severity_group = severity_groups.setdefault(
            severity_name,
            {"advisory_count": 0, "link_count": 0},
        )
        severity_group["advisory_count"] += advisory_count
        severity_group["link_count"] += link_count

    by_ecosystem = [
        EcosystemExposure(
            ecosystem=ecosystem_name,
            advisory_count=group["advisory_count"],
            link_count=group["link_count"],
            max_severity=max(group["severities"], key=_severity_rank),
            high_severity_links=group["high_severity_links"],
        )
        for ecosystem_name, group in sorted(ecosystem_groups.items())
    ]

    by_severity = [
        SeverityExposure(
            severity=severity_name,
            advisory_count=group["advisory_count"],
            link_count=group["link_count"],
        )
        for severity_name, group in sorted(
            severity_groups.items(),
            key=lambda item: (_severity_rank(item[0]), item[0]),
        )
    ]

    top_exposed_servers = [
        ExposedServer(
            server_id=str(row["server_id"]),
            name=row["name"],
            risk_tier=row["risk_tier"],
            link_count=int(row["link_count"] or 0),
            max_match_confidence=float(row["max_match_confidence"] or 0.0),
        )
        for row in server_rows
        if row["server_id"] is not None
    ]
    top_exposed_servers.sort(
        key=lambda server: (
            -server.link_count,
            -server.max_match_confidence,
            server.server_id,
        )
    )

    return EcosystemExposureResponse(
        total_links=sum(group["link_count"] for group in ecosystem_groups.values()),
        by_ecosystem=by_ecosystem,
        by_severity=by_severity,
        top_exposed_servers=top_exposed_servers,
    )


if __name__ == "__main__":
    test_app = FastAPI()
    test_app.include_router(router)
    assert any(
        route.path == "/api/vulns/ecosystem-exposure"
        for route in test_app.routes
    )
    print("PASS")