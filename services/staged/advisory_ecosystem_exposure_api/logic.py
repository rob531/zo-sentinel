from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session

router = APIRouter()


class EcosystemExposureBucket(BaseModel):
    ecosystem: str
    advisory_count: int
    link_count: int
    max_severity: str
    high_severity_links: int


class SeverityExposureBucket(BaseModel):
    severity: str
    advisory_count: int
    link_count: int


class ExposedServerBucket(BaseModel):
    server_id: Optional[str]
    name: Optional[str]
    risk_tier: Optional[str]
    link_count: int
    max_match_confidence: Optional[float]


class EcosystemExposureResponse(BaseModel):
    total_links: int
    by_ecosystem: List[EcosystemExposureBucket] = Field(default_factory=list)
    by_severity: List[SeverityExposureBucket] = Field(default_factory=list)
    top_exposed_servers: List[ExposedServerBucket] = Field(default_factory=list)


_ADVISORY_SELECTION_CTE = """
WITH advisory_link_counts AS (
    SELECT
        va.id,
        va.ecosystem,
        va.severity,
        va.published_at,
        COUNT(vl.id) AS link_count
    FROM vuln_advisories AS va
    LEFT JOIN vuln_links AS vl
        ON vl.advisory_id = va.id
    WHERE (
        :severity IS NULL
        OR LOWER(COALESCE(va.severity, 'unknown')) = :severity
    )
      AND (
        :ecosystem IS NULL
        OR LOWER(COALESCE(va.ecosystem, 'unknown')) = :ecosystem
    )
    GROUP BY
        va.id,
        va.ecosystem,
        va.severity,
        va.published_at
),
selected_advisories AS (
    SELECT
        id,
        ecosystem,
        severity,
        published_at
    FROM advisory_link_counts
    WHERE link_count > 0
    ORDER BY published_at DESC NULLS LAST, id
    LIMIT :advisory_limit
)
"""

_ECOSYSTEM_QUERY = (
    _ADVISORY_SELECTION_CTE
    + """
SELECT
    sa.ecosystem,
    sa.severity,
    COUNT(DISTINCT sa.id) AS advisory_count,
    COUNT(vl.id) AS link_count
FROM selected_advisories AS sa
LEFT JOIN vuln_links AS vl
    ON vl.advisory_id = sa.id
GROUP BY sa.ecosystem, sa.severity
"""
)

_SERVER_QUERY = (
    _ADVISORY_SELECTION_CTE
    + """
SELECT
    vl.server_id,
    ms.name,
    ms.risk_tier,
    COUNT(vl.id) AS link_count,
    MAX(vl.match_confidence) AS max_match_confidence
FROM selected_advisories AS sa
LEFT JOIN vuln_links AS vl
    ON vl.advisory_id = sa.id
LEFT JOIN mcp_server_registry AS ms
    ON ms.server_id = vl.server_id
WHERE vl.id IS NOT NULL
GROUP BY vl.server_id, ms.name, ms.risk_tier
"""
)

_SEVERITY_RANK = {
    "critical": 6,
    "high": 5,
    "medium": 4,
    "moderate": 4,
    "low": 3,
    "informational": 2,
    "info": 2,
    "unknown": 0,
}


def _normalized_severity(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    return normalized or "unknown"


def _normalized_ecosystem(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower()
    return normalized or "unknown"


def _severity_sort_key(value: str) -> tuple:
    return (-_SEVERITY_RANK.get(value, 0), value)


@router.get(
    "/api/vulns/ecosystem-exposure",
    response_model=EcosystemExposureResponse,
)
def get_ecosystem_exposure(
    severity: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=0),
    session=Depends(get_session),
) -> EcosystemExposureResponse:
    params = {
        "severity": severity.strip().lower() if severity else None,
        "ecosystem": ecosystem.strip().lower() if ecosystem else None,
        "advisory_limit": limit,
    }

    ecosystem_rows = session.execute(
        text(_ECOSYSTEM_QUERY),
        params,
    ).mappings().all()

    ecosystem_data: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "advisory_count": 0,
            "link_count": 0,
            "severities": {},
            "high_severity_links": 0,
        }
    )
    severity_data: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"advisory_count": 0, "link_count": 0}
    )
    total_links = 0

    for row in ecosystem_rows:
        normalized_ecosystem = _normalized_ecosystem(row["ecosystem"])
        normalized_severity = _normalized_severity(row["severity"])
        advisory_count = int(row["advisory_count"] or 0)
        link_count = int(row["link_count"] or 0)

        bucket = ecosystem_data[normalized_ecosystem]
        bucket["advisory_count"] += advisory_count
        bucket["link_count"] += link_count
        bucket["severities"][normalized_severity] = (
            bucket["severities"].get(normalized_severity, 0) + link_count
        )
        if normalized_severity in {"critical", "high"}:
            bucket["high_severity_links"] += link_count

        severity_bucket = severity_data[normalized_severity]
        severity_bucket["advisory_count"] += advisory_count
        severity_bucket["link_count"] += link_count
        total_links += link_count

    by_ecosystem = [
        EcosystemExposureBucket(
            ecosystem=name,
            advisory_count=data["advisory_count"],
            link_count=data["link_count"],
            max_severity=max(
                data["severities"].keys(),
                key=_severity_sort_key,
            ),
            high_severity_links=data["high_severity_links"],
        )
        for name, data in ecosystem_data.items()
    ]
    by_ecosystem.sort(key=lambda item: (-item.link_count, item.ecosystem))

    by_severity = [
        SeverityExposureBucket(
            severity=name,
            advisory_count=data["advisory_count"],
            link_count=data["link_count"],
        )
        for name, data in severity_data.items()
    ]
    by_severity.sort(key=lambda item: _severity_sort_key(item.severity))

    server_rows = session.execute(
        text(_SERVER_QUERY),
        params,
    ).mappings().all()

    top_exposed_servers = [
        ExposedServerBucket(
            server_id=(
                str(row["server_id"]) if row["server_id"] is not None else None
            ),
            name=row["name"],
            risk_tier=row["risk_tier"],
            link_count=int(row["link_count"] or 0),
            max_match_confidence=(
                float(row["max_match_confidence"])
                if row["max_match_confidence"] is not None
                else None
            ),
        )
        for row in server_rows
    ]
    top_exposed_servers.sort(
        key=lambda item: (
            -item.link_count,
            -(item.max_match_confidence or 0.0),
            item.server_id or "",
        )
    )

    return EcosystemExposureResponse(
        total_links=total_links,
        by_ecosystem=by_ecosystem,
        by_severity=by_severity,
        top_exposed_servers=top_exposed_servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    assert any(
        getattr(route, "path", None) == "/api/vulns/ecosystem-exposure"
        for route in that_app.routes
    )
    print("PASS")