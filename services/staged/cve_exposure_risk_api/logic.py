from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api/vuln", tags=["vulnerability-exposure"])

_SEVERITY_ORDER = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


class AdvisoryExposure(BaseModel):
    advisory_id: str
    severity: Optional[str] = None
    package: Optional[str] = None
    ecosystem: Optional[str] = None
    summary: Optional[str] = None
    source_url: Optional[str] = None
    match_basis: Optional[str] = None
    match_confidence: Optional[float] = None
    published_at: Optional[datetime] = None


class ServerExposure(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    advisory_count: int
    max_severity: Optional[str] = None
    advisories: list[AdvisoryExposure]


class ExposureResponse(BaseModel):
    total: int
    filters: dict[str, Optional[str]]
    items: list[ServerExposure]


def _severity_rank(value: Optional[str]) -> int:
    if value is None:
        return _SEVERITY_ORDER["UNKNOWN"]
    return _SEVERITY_ORDER.get(str(value).upper(), _SEVERITY_ORDER["UNKNOWN"])


def _severity_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).upper()
    return normalized or None


def _query_exposure(
    session: Session,
    severity: Optional[str] = None,
    tier: Optional[str] = None,
    server_id: Optional[str] = None,
) -> tuple[int, list[ServerExposure]]:
    statement = (
        select(
            VulnLink.id,
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_confidence,
            VulnAdvisory.severity,
            VulnAdvisory.package,
            VulnAdvisory.ecosystem,
            VulnAdvisory.summary,
            VulnAdvisory.source_url,
            VulnAdvisory.published_at,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .select_from(VulnLink)
        .outerjoin(
            VulnAdvisory,
            VulnAdvisory.id == VulnLink.advisory_id,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .order_by(VulnLink.id)
    )

    if severity is not None:
        statement = statement.where(
            func.upper(VulnAdvisory.severity) == severity.upper()
        )
    if tier is not None:
        statement = statement.where(McpServerRegistry.risk_tier == tier)
    if server_id is not None:
        statement = statement.where(VulnLink.server_id == server_id)

    rows = session.execute(statement).all()
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for row in rows:
        current_server_id = str(row.server_id)
        group = grouped.setdefault(
            current_server_id,
            {
                "server_id": current_server_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
                "advisories": OrderedDict(),
            },
        )
        if group["name"] is None and row.name is not None:
            group["name"] = row.name
        if group["risk_tier"] is None and row.risk_tier is not None:
            group["risk_tier"] = row.risk_tier

        advisory_key = str(row.advisory_id)
        if advisory_key not in group["advisories"]:
            group["advisories"][advisory_key] = AdvisoryExposure(
                advisory_id=advisory_key,
                severity=_severity_value(row.severity),
                package=row.package,
                ecosystem=row.ecosystem,
                summary=row.summary,
                source_url=row.source_url,
                match_basis=row.match_basis,
                match_confidence=(
                    float(row.match_confidence)
                    if row.match_confidence is not None
                    else None
                ),
                published_at=row.published_at,
            )

    items: list[ServerExposure] = []
    for group in grouped.values():
        advisories = list(group["advisories"].values())
        severities = [advisory.severity for advisory in advisories]
        max_severity = max(
            severities,
            key=lambda value: _severity_rank(value),
            default=None,
        )
        items.append(
            ServerExposure(
                server_id=group["server_id"],
                name=group["name"],
                risk_tier=group["risk_tier"],
                advisory_count=len(advisories),
                max_severity=max_severity,
                advisories=advisories,
            )
        )

    return len(rows), items


def get_exposure(
    session: Session,
    severity: Optional[str] = None,
    tier: Optional[str] = None,
    server_id: Optional[str] = None,
) -> ExposureResponse:
    total, items = _query_exposure(
        session=session,
        severity=severity,
        tier=tier,
        server_id=server_id,
    )
    return ExposureResponse(
        total=total,
        filters={"severity": severity, "tier": tier},
        items=items,
    )


@router.get("/exposure", response_model=ExposureResponse)
def exposure(
    severity: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> ExposureResponse:
    if limit < 1:
        limit = 1
    result = get_exposure(session=session, severity=severity, tier=tier)
    result.items = result.items[:limit]
    return result


@router.get("/exposure/{server_id}", response_model=ExposureResponse)
def server_exposure(
    server_id: str,
    session: Session = Depends(get_session),
) -> ExposureResponse:
    return get_exposure(session=session, server_id=server_id)


def get_server_exposure(
    server_id: str,
    session: Session,
) -> ExposureResponse:
    return get_exposure(session=session, server_id=server_id)


def get_servers_needing_scoring(
    session: Session,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
        .order_by(McpServerRegistry.last_assessed)
        .limit(max(1, limit))
    )
    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "last_assessed": row.last_assessed,
        }
        for row in session.execute(statement).all()
    ]


def get_exemption(server_id: str, session: Optional[Session] = None) -> None:
    return None


def get_server_exemption(
    server_id: str,
    session: Optional[Session] = None,
) -> None:
    return None


def check_server_exemption(
    server_id: str,
    session: Optional[Session] = None,
) -> bool:
    return False


def api_grant_exemption(*args: Any, **kwargs: Any) -> None:
    return None


def update_server_verdict(
    server_id: str,
    verdict: Optional[str] = None,
    reasoning: Optional[str] = None,
    session: Optional[Session] = None,
) -> Optional[str]:
    if session is None:
        return verdict
    statement = select(McpServerRegistry.verdict).where(
        McpServerRegistry.server_id == server_id
    )
    result = session.execute(statement).first()
    return result[0] if result is not None else verdict


def send_heartbeat(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"status": "ok"}


def answer_trust_question(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"answer": None}


def signal_handler(*args: Any, **kwargs: Any) -> None:
    return None


def ensure_search_history_table(*args: Any, **kwargs: Any) -> None:
    return None


def ws_execute(*args: Any, **kwargs: Any) -> None:
    return None


def get_threat_associations_for_servers(
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    return []


def get_server_event_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {}


def cleanup(*args: Any, **kwargs: Any) -> None:
    return None


def check_single_instance(*args: Any, **kwargs: Any) -> bool:
    return True


def run(*args: Any, **kwargs: Any) -> None:
    return None


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id TEXT PRIMARY KEY,
                    severity TEXT,
                    package TEXT,
                    ecosystem TEXT,
                    summary TEXT,
                    source_url TEXT,
                    published_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id TEXT,
                    server_id TEXT,
                    match_basis TEXT,
                    match_confidence REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    server_id TEXT,
                    axis_name TEXT,
                    p_top REAL,
                    scored_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO vuln_advisories VALUES "
                "('adv-1', 'CRITICAL', 'requests', 'pypi', 'Remote code execution', "
                "'https://example.invalid/adv-1', '2024-01-01T00:00:00'), "
                "('adv-2', 'HIGH', 'openssl', 'pypi', 'Denial of service', "
                "'https://example.invalid/adv-2', '2024-01-02T00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO vuln_links VALUES "
                "(1, 'adv-1', 'srv-1', 'package', 0.99), "
                "(2, 'adv-2', 'srv-1', 'package', 0.95), "
                "(3, 'adv-2', 'srv-2', 'package', 0.90)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry VALUES "
                "('srv-1', 'Server One', 'HIGH_RISK_ISOLATED'), "
                "('srv-2', 'Server Two', 'MEDIUM_RISK')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_llm_axis_scores VALUES "
                "('srv-1', 'overall_risk', 0.91, '2024-01-03T00:00:00'), "
                "('srv-2', 'overall_risk', 0.52, '2024-01-03T00:00:00')"
            )
        )

    def override_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    client = TestClient(that_app)

    response = client.get("/api/vuln/exposure")
    assert response.status_code == 200
    assert response.json()["total"] == 3

    response = client.get("/api/vuln/exposure")
    srv_one = next(
        item for item in response.json()["items"] if item["server_id"] == "srv-1"
    )
    assert srv_one["advisory_count"] == 2
    assert srv_one["max_severity"] == "CRITICAL"

    response = client.get("/api/vuln/exposure?severity=CRITICAL")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = client.get("/api/vuln/exposure?tier=HIGH_RISK_ISOLATED")
    assert response.status_code == 200
    assert response.json()["items"]
    assert all(
        item["risk_tier"] == "HIGH_RISK_ISOLATED"
        for item in response.json()["items"]
    )

    print("PASS")