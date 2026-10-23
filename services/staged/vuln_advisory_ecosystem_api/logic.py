from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter()

_SEVERITY_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class EcosystemServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    match_confidence: float
    advisory_id: str


class EcosystemExposure(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_server_count: int
    max_severity: str
    servers: List[EcosystemServer]


class EcosystemResponse(BaseModel):
    days: int
    generated_at: str
    ecosystems: List[EcosystemExposure]


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "_mapping"):
        return row._mapping.get(key, default)
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return getattr(row, key, default)


def _severity_rank(value: Any) -> int:
    return _SEVERITY_ORDER.get(str(value or "none").strip().lower(), 0)


def _aggregate_ecosystems(rows: Iterable[Any]) -> List[EcosystemExposure]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        ecosystem = str(_row_value(row, "ecosystem", "") or "")
        advisory_id = str(_row_value(row, "advisory_id", "") or "")
        severity = str(_row_value(row, "severity", "none") or "none").lower()

        bucket = grouped.setdefault(
            ecosystem,
            {
                "advisory_ids": set(),
                "server_ids": set(),
                "severity_rank": 0,
                "servers": [],
            },
        )

        if advisory_id:
            bucket["advisory_ids"].add(advisory_id)
        bucket["severity_rank"] = max(
            bucket["severity_rank"], _severity_rank(severity)
        )

        server_id = _row_value(row, "server_id")
        if server_id:
            server_id = str(server_id)
            bucket["server_ids"].add(server_id)
            confidence = _row_value(row, "match_confidence", 0.0)
            try:
                confidence = float(confidence or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0

            bucket["servers"].append(
                EcosystemServer(
                    server_id=server_id,
                    name=str(_row_value(row, "name", "") or ""),
                    risk_tier=str(_row_value(row, "risk_tier", "") or ""),
                    match_confidence=confidence,
                    advisory_id=advisory_id,
                )
            )

    result: List[EcosystemExposure] = []
    for ecosystem, bucket in grouped.items():
        severity_rank = bucket["severity_rank"]
        max_severity = "none"
        for candidate, rank in _SEVERITY_ORDER.items():
            if rank == severity_rank:
                max_severity = candidate
                break

        result.append(
            EcosystemExposure(
                ecosystem=ecosystem,
                advisory_count=len(bucket["advisory_ids"]),
                linked_server_count=len(bucket["server_ids"]),
                max_severity=max_severity,
                servers=bucket["servers"],
            )
        )

    result.sort(key=lambda item: item.ecosystem)
    return result


def get_ecosystem_exposure(
    days: int = 30,
    severity: Optional[str] = None,
    session: Any = Depends(get_session),
) -> EcosystemResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    params: Dict[str, Any] = {"cutoff": cutoff}

    where = ["va.published_at >= :cutoff"]
    if severity is not None:
        where.append("LOWER(va.severity) = :severity")
        params["severity"] = severity.strip().lower()

    statement = text(
        """
        SELECT
            va.id AS advisory_id,
            va.ecosystem AS ecosystem,
            va.severity AS severity,
            vl.server_id AS server_id,
            msr.name AS name,
            msr.risk_tier AS risk_tier,
            vl.match_confidence AS match_confidence
        FROM vuln_advisories AS va
        LEFT JOIN vuln_links AS vl
            ON vl.advisory_id = va.id
        LEFT JOIN mcp_server_registry AS msr
            ON msr.server_id = vl.server_id
        WHERE
            """
        + " AND ".join(where)
        + """
        ORDER BY va.ecosystem, va.id, vl.server_id
        """
    )

    rows = session.execute(statement, params)
    return EcosystemResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        ecosystems=_aggregate_ecosystems(rows),
    )


@router.get(
    "/vuln/ecosystem",
    response_model=EcosystemResponse,
    summary="Aggregate vulnerability advisory exposure by ecosystem",
)
def ecosystem_exposure(
    days: int = Query(30, ge=0),
    severity: Optional[str] = Query(None),
    session: Any = Depends(get_session),
) -> EcosystemResponse:
    return get_ecosystem_exposure(days=days, severity=severity, session=session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    class _Session:
        def __init__(self):
            now = datetime.now(timezone.utc)
            self.advisories = [
                {
                    "id": "adv-1",
                    "ecosystem": "npm",
                    "severity": "critical",
                    "published_at": now,
                },
                {
                    "id": "adv-2",
                    "ecosystem": "npm",
                    "severity": "high",
                    "published_at": now,
                },
                {
                    "id": "adv-3",
                    "ecosystem": "pypi",
                    "severity": "high",
                    "published_at": now,
                },
            ]
            self.links = [
                {
                    "advisory_id": "adv-1",
                    "server_id": "server-1",
                    "match_confidence": 0.97,
                },
                {
                    "advisory_id": "adv-2",
                    "server_id": "server-2",
                    "match_confidence": 0.91,
                },
            ]
            self.registry = [
                {"server_id": "server-1", "name": "Payments MCP", "risk_tier": "high"},
                {"server_id": "server-2", "name": "Search MCP", "risk_tier": "medium"},
            ]

        def execute(self, statement, params):
            sql = str(statement)
            severity = params.get("severity")
            cutoff = params.get("cutoff")
            advisories = [
                advisory
                for advisory in self.advisories
                if advisory["published_at"] >= cutoff
                and (
                    severity is None
                    or advisory["severity"].lower() == severity.lower()
                )
            ]
            rows = []
            for advisory in advisories:
                links = [
                    link
                    for link in self.links
                    if link["advisory_id"] == advisory["id"]
                ]
                if not links:
                    rows.append(
                        {
                            "advisory_id": advisory["id"],
                            "ecosystem": advisory["ecosystem"],
                            "severity": advisory["severity"],
                            "server_id": None,
                            "name": None,
                            "risk_tier": None,
                            "match_confidence": None,
                        }
                    )
                for link in links:
                    registry = next(
                        (
                            item
                            for item in self.registry
                            if item["server_id"] == link["server_id"]
                        ),
                        {},
                    )
                    rows.append(
                        {
                            "advisory_id": advisory["id"],
                            "ecosystem": advisory["ecosystem"],
                            "severity": advisory["severity"],
                            "server_id": link["server_id"],
                            "name": registry.get("name", ""),
                            "risk_tier": registry.get("risk_tier", ""),
                            "match_confidence": link["match_confidence"],
                        }
                    )
            return _Result(rows)

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    session = _Session()
    test_app.dependency_overrides[get_session] = lambda: session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/ecosystem")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["ecosystems"]) == 2, payload

        npm = next(
            item for item in payload["ecosystems"] if item["ecosystem"] == "npm"
        )
        assert npm["advisory_count"] == 2, npm
        assert npm["linked_server_count"] == 2, npm
        assert npm["max_severity"] == "critical", npm

        filtered = client.get(
            "/api/vuln/ecosystem",
            params={"severity": "critical"},
        )
        assert filtered.status_code == 200, filtered.text
        assert len(filtered.json()["ecosystems"]) == 1, filtered.json()

    print("PASS")