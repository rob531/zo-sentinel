from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class SeverityMatrixCell(BaseModel):
    severity: str
    ecosystem: str
    advisory_count: int
    server_count: int
    high_risk_servers: int


class TopSeverity(BaseModel):
    severity: str
    count: int


class SeverityMatrixResponse(BaseModel):
    total_advisories: int
    total_links: int
    matrix: list[SeverityMatrixCell]
    top_severities: list[TopSeverity]


router = APIRouter(prefix="/api")


@router.get("/vulns/severity-matrix", response_model=SeverityMatrixResponse)
def get_severity_matrix(session: Session = Depends(get_session)) -> SeverityMatrixResponse:
    rows = session.execute(
        select(
            VulnAdvisory.severity.label("severity"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            func.count(func.distinct(VulnAdvisory.id)).label("advisory_count"),
            func.count(func.distinct(VulnLink.server_id)).label("server_count"),
            func.count(
                func.distinct(
                    case(
                        (
                            McpServerRegistry.risk_tier == "HIGH_RISK_ISOLATED",
                            VulnLink.server_id,
                        ),
                        else_=None,
                    )
                )
            ).label("high_risk_servers"),
        )
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .group_by(VulnAdvisory.severity, VulnAdvisory.ecosystem)
        .order_by(
            VulnAdvisory.severity.desc(),
            func.count(func.distinct(VulnAdvisory.id)).desc(),
            VulnAdvisory.ecosystem.asc(),
        )
    ).all()

    total_advisories = session.scalar(
        select(func.count(func.distinct(VulnAdvisory.id)))
    ) or 0
    total_links = session.scalar(
        select(func.count(VulnLink.advisory_id))
    ) or 0

    matrix = [
        SeverityMatrixCell(
            severity=row.severity,
            ecosystem=row.ecosystem,
            advisory_count=row.advisory_count,
            server_count=row.server_count,
            high_risk_servers=row.high_risk_servers,
        )
        for row in rows
    ]

    severity_counts: dict[str, int] = {}
    for cell in matrix:
        severity_counts[cell.severity] = (
            severity_counts.get(cell.severity, 0) + cell.advisory_count
        )

    top_severities = [
        TopSeverity(severity=severity, count=count)
        for severity, count in sorted(
            severity_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return SeverityMatrixResponse(
        total_advisories=total_advisories,
        total_links=total_links,
        matrix=matrix,
        top_severities=top_severities,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    advisories = Table(
        "vuln_advisories",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("severity", String, nullable=False),
        Column("ecosystem", String, nullable=False),
    )
    links = Table(
        "vuln_links",
        metadata,
        Column("advisory_id", Integer, primary_key=True),
        Column("server_id", Integer, primary_key=True),
        Column("match_confidence", String),
    )
    servers = Table(
        "mcp_server_registry",
        metadata,
        Column("server_id", Integer, primary_key=True),
        Column("risk_tier", String, nullable=False),
    )
    metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        session.execute(
            advisories.insert(),
            [
                {"id": 1, "severity": "critical", "ecosystem": "npm"},
                {"id": 2, "severity": "high", "ecosystem": "npm"},
                {"id": 3, "severity": "medium", "ecosystem": "pypi"},
            ],
        )
        session.execute(
            servers.insert(),
            [
                {"server_id": 1, "risk_tier": "HIGH_RISK_ISOLATED"},
                {"server_id": 2, "risk_tier": "STANDARD"},
                {"server_id": 3, "risk_tier": "STANDARD"},
                {"server_id": 4, "risk_tier": "STANDARD"},
            ],
        )
        session.execute(
            links.insert(),
            [
                {"advisory_id": 1, "server_id": 1, "match_confidence": "1.0"},
                {"advisory_id": 2, "server_id": 2, "match_confidence": "1.0"},
                {"advisory_id": 3, "server_id": 3, "match_confidence": "1.0"},
                {"advisory_id": 2, "server_id": 4, "match_confidence": "1.0"},
            ],
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vulns/severity-matrix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 3
        assert payload["total_links"] == 4

        critical = next(
            row
            for row in payload["matrix"]
            if row["severity"] == "critical"
            and row["ecosystem"] == "npm"
        )
        assert critical["advisory_count"] == 1
        assert critical["high_risk_servers"] == 1

    engine.dispose()
    print("PASS")