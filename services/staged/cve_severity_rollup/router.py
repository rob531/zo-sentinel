# services/staged/cve_severity_rollup/router.py
"""cve_severity_rollup service."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, func
from sqlalchemy.orm import sessionmaker
from app.db import get_session
from app.models import VulnAdvisory, VulnLink, McpServerRegistry

router = APIRouter(prefix="/api", tags=["cve"])


class SeverityRollupResponse(BaseModel):
    total_servers: int
    by_severity: dict[str, int]


@router.get("/cve/severity-rollup", response_model=SeverityRollupResponse)
def get_severity_rollup(session=Depends(get_session)) -> SeverityRollupResponse:
    """Count servers per severity bucket across the full registry."""
    result = (
        session.query(
            VulnAdvisory.severity,
            func.count(func.distinct(VulnLink.server_id)).label("server_count"),
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.severity)
        .all()
    )

    by_severity = {sev: 0 for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")}
    total_servers = 0

    for row in result:
        severity, count = row
        if severity in by_severity:
            by_severity[severity] = count
        total_servers += count

    return SeverityRollupResponse(total_servers=total_servers, by_severity=by_severity)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import MetaData, Table
    from sqlalchemy.orm import Session
    from starlETTE.testclient import TestClient

    # In-memory test store
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    test_session = SessionLocal()

    # Define tables
    metadata = MetaData()
    server_registry = Table(
        "McpServerRegistry",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String),
        Column("server_name", String),
    )
    vuln_advisories = Table(
        "vuln_advisories",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("advisory_id", String),
        Column("cve", String),
        Column("severity", String),
    )
    vuln_links = Table(
        "vuln_links",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", Integer),
        Column("advisory_id", Integer),
    )
    metadata.create_all(engine)

    # Seed: 3 servers, advisories of mixed severity
    test_session.execute(server_registry.insert(), [
        {"server_id": "s1", "server_name": "server-1"},
        {"server_id": "s2", "server_name": "server-2"},
        {"server_id": "s3", "server_name": "server-3"},
    ])
    test_session.execute(vuln_advisories.insert(), [
        {"advisory_id": "a1", "cve": "CVE-2021-1", "severity": "CRITICAL"},
        {"advisory_id": "a2", "cve": "CVE-2022-2", "severity": "HIGH"},
        {"advisory_id": "a3", "cve": "CVE-2023-3", "severity": "MEDIUM"},
    ])
    test_session.execute(vuln_links.insert(), [
        {"server_id": 1, "advisory_id": 1},
        {"server_id": 1, "advisory_id": 2},
        {"server_id": 2, "advisory_id": 2},
        {"server_id": 2, "advisory_id": 3},
        {"server_id": 3, "advisory_id": 3},
    ])
    test_session.commit()

    # Override app dependency
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: test_session

    # Test
    with TestClient(app) as client:
        response = client.get("/api/cve/severity-rollup")
        assert response.status_code == 200
        data = response.json()
        assert data["total_servers"] == 3
        assert data["by_severity"]["CRITICAL"] >= 1

    print("PASS")