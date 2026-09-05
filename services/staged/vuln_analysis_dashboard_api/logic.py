from typing import Optional
from collections import defaultdict

from fastapi import Depends
from sqlalchemy import func, text, Table, Column, String, Integer, MetaData
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db import get_session

try:
    from app.models import VulnAdvisory, VulnLink, McpServerRegistry
    USE_ORM = True
except ImportError:
    USE_ORM = False

class TopAdvisory(BaseModel):
    id: str
    severity: str
    count: int

class VulnAnalysisDashboardResponse(BaseModel):
    severity_counts: dict[str, int]
    tier_distribution: dict[str, int]
    top_advisories: list[TopAdvisory]

def get_vuln_analysis_dashboard(
    session: Session = Depends(get_session),
) -> VulnAnalysisDashboardResponse:
    if USE_ORM:
        severity_counts_q = (
            session.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
            .group_by(VulnAdvisory.severity)
        )
        tier_dist_q = (
            session.query(McpServerRegistry.risk_tier, func.count(VulnLink.id))
            .join(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
            .group_by(McpServerRegistry.risk_tier)
        )
        top_q = (
            session.query(
                VulnAdvisory.id,
                VulnAdvisory.severity,
                func.count(VulnLink.server_id.distinct()).label("cnt")
            )
            .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
            .group_by(VulnAdvisory.id, VulnAdvisory.severity)
            .order_by(text("cnt DESC"))
            .limit(10)
        )
    else:
        metadata = MetaData()
        t_advisories = Table('vuln_advisories', metadata,
            Column('id', String),
            Column('severity', String),
            Column('summary', String),
        )
        t_links = Table('vuln_links', metadata,
            Column('id', Integer),
            Column('advisory_id', String),
            Column('server_id', String),
        )
        t_registry = Table('mcp_server_registry', metadata,
            Column('server_id', String),
            Column('name', String),
            Column('risk_tier', String),
        )
        severity_counts_q = (
            session.query(t_advisories.c.severity, func.count(t_advisories.c.id))
            .join(t_links, t_links.c.advisory_id == t_advisories.c.id)
            .group_by(t_advisories.c.severity)
        )
        tier_dist_q = (
            session.query(t_registry.c.risk_tier, func.count(t_links.c.id))
            .join(t_links, t_links.c.server_id == t_registry.c.server_id)
            .group_by(t_registry.c.risk_tier)
        )
        top_q = (
            session.query(
                t_advisories.c.id,
                t_advisories.c.severity,
                func.count(t_links.c.server_id.distinct()).label("cnt")
            )
            .join(t_links, t_links.c.advisory_id == t_advisories.c.id)
            .group_by(t_advisories.c.id, t_advisories.c.severity)
            .order_by(text("cnt DESC"))
            .limit(10)
        )

    severity_counts = defaultdict(int)
    for row in severity_counts_q.all():
        severity_counts[row[0]] = row[1]

    tier_distribution = defaultdict(int)
    for row in tier_dist_q.all():
        tier_distribution[row[0]] = row[1]

    top_advisories = []
    for row in top_q.all():
        top_advisories.append(TopAdvisory(id=row[0], severity=row[1], count=row[2]))

    return VulnAnalysisDashboardResponse(
        severity_counts=dict(severity_counts),
        tier_distribution=dict(tier_distribution),
        top_advisories=top_advisories,
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                risk_tier VARCHAR,
                url VARCHAR,
                description VARCHAR,
                registry_source VARCHAR,
                trust_score FLOAT,
                confidence FLOAT,
                verdict VARCHAR,
                verdict_reasoning VARCHAR,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id VARCHAR PRIMARY KEY,
                severity VARCHAR,
                summary VARCHAR,
                package VARCHAR,
                ecosystem VARCHAR,
                feed VARCHAR,
                source_url VARCHAR,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP,
                content_hash VARCHAR,
                affected_ranges TEXT,
                aliases TEXT,
                identities TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                advisory_id VARCHAR,
                server_id VARCHAR,
                match_value VARCHAR,
                match_basis VARCHAR,
                match_confidence FLOAT,
                linked_at TIMESTAMP
            )
        """))

        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES
            ('srv_001', 'Server Alpha', 'high'),
            ('srv_002', 'Server Beta', 'low')
        """))
        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, summary) VALUES
            ('ADV001', 'CRITICAL', 'Critical vulnerability in core library'),
            ('ADV002', 'HIGH', 'High severity issue in authentication'),
            ('ADV003', 'MEDIUM', 'Medium risk advisory in network module'),
            ('ADV004', 'LOW', 'Low impact vulnerability in logging'),
            ('ADV005', 'HIGH', 'Another high severity issue in crypto')
        """))
        conn.execute(text("""
            INSERT INTO vuln_links (advisory_id, server_id, match_confidence) VALUES
            ('ADV001', 'srv_001', 0.95),
            ('ADV002', 'srv_001', 0.90),
            ('ADV003', 'srv_001', 0.85),
            ('ADV004', 'srv_001', 0.80),
            ('ADV005', 'srv_002', 0.88)
        """))
        conn.commit()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    result = get_vuln_analysis_dashboard(override_get_session().__next__())

    severity_checks = {
        'CRITICAL': False,
        'HIGH': False,
        'MEDIUM': False,
        'LOW': False,
    }
    for sev, cnt in result.severity_counts.items():
        if sev in severity_checks and cnt > 0:
            severity_checks[sev] = True

    all_severities_ok = all(severity_checks.values())

    if result.tier_distribution and result.top_advisories and all_severities_ok:
        print("PASS")
    else:
        print(f"FAIL: counts={result.severity_counts}, tiers={result.tier_distribution}, top={result.top_advisories}")