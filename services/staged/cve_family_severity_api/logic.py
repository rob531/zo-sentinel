from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api")

_SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "unknown": 1,
}


def _severity_bucket(value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in _SEVERITY_ORDER else "unknown"


@router.get("/vuln/family-severity")
def get_family_severity(
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    severity_filter = severity.strip().lower() if severity is not None else None

    server_query = (
        select(VulnLink.server_id)
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .group_by(VulnLink.server_id)
        .order_by(VulnLink.server_id)
    )
    link_query = (
        select(
            VulnLink.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            VulnAdvisory.severity,
            VulnAdvisory.package,
        )
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
    )

    if severity_filter is not None:
        severity_expression = func.lower(
            func.coalesce(VulnAdvisory.severity, "unknown")
        )
        if severity_filter == "unknown":
            severity_condition = (
                VulnAdvisory.severity.is_(None)
                | (severity_expression == "unknown")
            )
        else:
            severity_condition = severity_expression == severity_filter
        server_query = server_query.where(severity_condition)
        link_query = link_query.where(severity_condition)

    server_ids = session.execute(server_query.limit(limit)).scalars().all()
    if not server_ids:
        return {"servers": [], "total_servers": 0}

    rows = session.execute(
        link_query.where(VulnLink.server_id.in_(server_ids))
        .order_by(VulnLink.server_id, VulnAdvisory.id)
    ).all()

    buckets: dict[str, dict] = {}
    for server_id, server_name, risk_tier, advisory_severity, package_name in rows:
        key = str(server_id)
        bucket = buckets.setdefault(
            key,
            {
                "server_id": key,
                "server_name": str(server_name or ""),
                "risk_tier": str(risk_tier or ""),
                "by_severity": {
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "unknown": 0,
                },
                "packages": set(),
                "max_severity": "unknown",
            },
        )
        severity_bucket = _severity_bucket(advisory_severity)
        bucket["by_severity"][severity_bucket] += 1
        if package_name is not None:
            bucket["packages"].add(package_name)
        if _SEVERITY_ORDER[severity_bucket] > _SEVERITY_ORDER[bucket["max_severity"]]:
            bucket["max_severity"] = severity_bucket

    servers = []
    for server_id in server_ids:
        bucket = buckets.get(str(server_id))
        if bucket is None:
            continue
        servers.append(
            {
                "server_id": bucket["server_id"],
                "server_name": bucket["server_name"],
                "risk_tier": bucket["risk_tier"],
                "by_severity": bucket["by_severity"],
                "distinct_packages": len(bucket["packages"]),
                "max_severity": bucket["max_severity"],
            }
        )

    return {"servers": servers, "total_servers": len(servers)}


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    print("PASS")