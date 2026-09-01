from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

app = FastAPI(title="cve_risk_summary", version="1.0.0")


@app.get("/api/cve/risk-summary")
def get_cve_risk_summary(session: Session = Depends(get_session)) -> dict[str, Any]:
    """
    Aggregate CVE risk summary from vuln_advisories joined to vuln_links to McpServerRegistry.
    Returns severity counts per server and overall ecosystem distribution.
    """
    # Subquery to join advisories -> links -> servers
    advisory_server_join = (
        select(
            VulnAdvisory.cve_id,
            VulnAdvisory.severity,
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.ecosystem,
        )
        .join(VulnLink, VulnLink.cve_id == VulnAdvisory.cve_id)
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
    ).subquery()

    # Total advisories
    total_advisories = session.execute(
        select(func.count(VulnAdvisory.cve_id)).select_from(VulnAdvisory)
    ).scalar() or 0

    # By severity counts
    severity_counts = {}
    for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]:
        count = session.execute(
            select(func.count(VulnAdvisory.cve_id))
            .select_from(VulnAdvisory)
            .where(VulnAdvisory.severity == severity)
        ).scalar() or 0
        severity_counts[severity] = count

    # Top affected servers
    top_servers_query = (
        select(
            advisory_server_join.c.server_id,
            advisory_server_join.c.name,
            advisory_server_join.c.severity,
        )
    ).subquery()

    server_severities = session.execute(
        select(
            top_servers_query.c.server_id,
            top_servers_query.c.name,
            func.count(top_servers_query.c.cve_id).label("vuln_count"),
            func.json_group_array(top_servers_query.c.severity).label("severities"),
        )
        .select_from(top_servers_query)
        .group_by(top_servers_query.c.server_id, top_servers_query.c.name)
        .order_by(func.count(top_servers_query.c.cve_id).desc())
    ).fetchall()

    top_affected_servers = []
    for row in server_severities:
        top_affected_servers.append({
            "server_id": row.server_id,
            "name": row.name,
            "severities": row.severities,
        })

    # Ecosystem breakdown
    ecosystem_data = session.execute(
        select(
            McpServerRegistry.ecosystem,
            func.count(advisory_server_join.c.cve_id).label("count"),
        )
        .select_from(advisory_server_join)
        .group_by(McpServerRegistry.ecosystem)
    ).fetchall()

    ecosystem_breakdown = {row.ecosystem: row.count for row in ecosystem_data}

    return {
        "total_advisories": total_advisories,
        "by_severity": severity_counts,
        "top_affected_servers": top_affected_servers,
        "ecosystem_breakdown": ecosystem_breakdown,
    }


def _create_test_db():
    """Create in-memory SQLite for self-test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return TestingSessionLocal


def _seed_data(session: Session):
    """Seed test data: 4 advisories across 3 servers."""
    # Servers
    server1 = McpServerRegistry(server_id="srv-001", name="server-alpha", ecosystem="npm")
    server2 = McpServerRegistry(server_id="srv-002", name="server-beta", ecosystem="pypi")
    server3 = McpServerRegistry(server_id="srv-003", name="server-gamma", ecosystem="docker")
    session.add_all([server1, server2, server3])
    session.flush()

    # Advisories with varying severities
    adv1 = VulnAdvisory(cve_id="CVE-2024-0001", severity="CRITICAL", title="Critical vuln", description="desc")
    adv2 = VulnAdvisory(cve_id="CVE-2024-0002", severity="HIGH", title="High vuln", description="desc")
    adv3 = VulnAdvisory(cve_id="CVE-2024-0003", severity="MEDIUM", title="Medium vuln", description="desc")
    adv4 = VulnAdvisory(cve_id="CVE-2024-0004", severity="LOW", title="Low vuln", description="desc")
    session.add_all([adv1, adv2, adv3, adv4])
    session.flush()

    # Links: distribute across servers
    link1 = VulnLink(cve_id="CVE-2024-0001", server_id="srv-001")
    link2 = VulnLink(cve_id="CVE-2024-0002", server_id="srv-001")
    link3 = VulnLink(cve_id="CVE-2024-0003", server_id="srv-002")
    link4 = VulnLink(cve_id="CVE-2024-0004", server_id="srv-003")
    session.add_all([link1, link2, link3, link4])
    session.commit()


if __name__ == "__main__":
    TestingSessionLocal = _create_test_db()

    def override_get_session():
        session = TestingSessionLocal()
        _seed_data(session)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/cve/risk-summary")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total_advisories"] == 4, f"Expected 4 advisories, got {data['total_advisories']}"
    assert data["by_severity"]["CRITICAL"] == 1, f"Expected CRITICAL=1, got {data['by_severity'].get('CRITICAL')}"
    assert data["by_severity"]["HIGH"] == 1, f"Expected HIGH=1, got {data['by_severity'].get('HIGH')}"
    assert data["by_severity"]["MEDIUM"] == 1, f"Expected MEDIUM=1, got {data['by_severity'].get('MEDIUM')}"
    assert data["by_severity"]["LOW"] == 1, f"Expected LOW=1, got {data['by_severity'].get('LOW')}"
    assert data["by_severity"]["UNKNOWN"] == 0, f"Expected UNKNOWN=0, got {data['by_severity'].get('UNKNOWN')}"

    # Verify servers
    assert len(data["top_affected_servers"]) == 3, f"Expected 3 servers, got {len(data['top_affected_servers'])}"

    # Verify ecosystem breakdown
    assert data["ecosystem_breakdown"].get("npm") == 2, f"Expected npm=2, got {data['ecosystem_breakdown']}"
    assert data["ecosystem_breakdown"].get("pypi") == 1, f"Expected pypi=1, got {data['ecosystem_breakdown']}"
    assert data["ecosystem_breakdown"].get("docker") == 1, f"Expected docker=1, got {data['ecosystem_breakdown']}"

    print("PASS")