from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnLink, VulnAdvisory


class ServerCVERiskEntry(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    severity: str
    cve_id: Optional[str]
    summary: Optional[str]


class CVERiskTiersResponse(BaseModel):
    servers: List[ServerCVERiskEntry]


def get_cve_risk_tiers(db: Session) -> CVERiskTiersResponse:
    low_risk_tiers = ['HIGH_RISK_ISOLATED', 'CAUTION_LIMITED']
    high_critical = ['HIGH', 'CRITICAL']

    query = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            VulnAdvisory.severity,
            VulnAdvisory.aliases,
            VulnAdvisory.summary,
        )
        .join(VulnLink, McpServerRegistry.server_id == VulnLink.server_id)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(
            and_(
                McpServerRegistry.risk_tier.in_(low_risk_tiers),
                VulnAdvisory.severity.in_(high_critical),
            )
        )
    )

    results = db.execute(query).fetchall()

    servers = []
    for row in results:
        cve_id = None
        if row.aliases:
            for alias in row.aliases:
                if isinstance(alias, str) and alias.startswith('CVE-'):
                    cve_id = alias
                    break

        servers.append(
            ServerCVERiskEntry(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                severity=row.severity,
                cve_id=cve_id,
                summary=row.summary,
            )
        )

    return CVERiskTiersResponse(servers=servers)


if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    registry = [
        {'server_id': 'srv-001', 'name': 'Bad Server 1', 'risk_tier': 'HIGH_RISK_ISOLATED', 'url': 'http://bad1.local', 'registry_source': 'test'},
        {'server_id': 'srv-002', 'name': 'Bad Server 2', 'risk_tier': 'CAUTION_LIMITED', 'url': 'http://bad2.local', 'registry_source': 'test'},
        {'server_id': 'srv-003', 'name': 'Clean Server 1', 'risk_tier': 'TRUSTED_STANDARD', 'url': 'http://clean1.local', 'registry_source': 'test'},
        {'server_id': 'srv-004', 'name': 'Clean Server 2', 'risk_tier': 'SANDBOXED_EXPERIMENTAL', 'url': 'http://clean2.local', 'registry_source': 'test'},
    ]
    for r in registry:
        session.add(McpServerRegistry(**r))

    advisories = [
        {'id': 'adv-001', 'severity': 'CRITICAL', 'aliases': ['CVE-2024-0001'], 'summary': 'Critical remote code execution', 'ecosystem': 'pypi', 'feed': 'test', 'published_at': None, 'fetched_at': None, 'identities': [], 'affected_ranges': [], 'package': 'bad-lib', 'source_url': 'http://test.local', 'content_hash': 'hash1'},
        {'id': 'adv-002', 'severity': 'HIGH', 'aliases': ['CVE-2024-0002'], 'summary': 'High severity SQL injection', 'ecosystem': 'pypi', 'feed': 'test', 'published_at': None, 'fetched_at': None, 'identities': [], 'affected_ranges': [], 'package': 'bad-lib', 'source_url': 'http://test.local', 'content_hash': 'hash2'},
        {'id': 'adv-003', 'severity': 'LOW', 'aliases': ['CVE-2024-0003'], 'summary': 'Low severity info leak', 'ecosystem': 'pypi', 'feed': 'test', 'published_at': None, 'fetched_at': None, 'identities': [], 'affected_ranges': [], 'package': 'clean-lib', 'source_url': 'http://test.local', 'content_hash': 'hash3'},
    ]
    for a in advisories:
        session.add(VulnAdvisory(**a))

    links = [
        {'server_id': 'srv-001', 'advisory_id': 'adv-001', 'match_value': 'bad-lib', 'match_basis': 'package', 'match_confidence': 0.95},
        {'server_id': 'srv-002', 'advisory_id': 'adv-002', 'match_value': 'bad-lib', 'match_basis': 'package', 'match_confidence': 0.90},
        {'server_id': 'srv-003', 'advisory_id': 'adv-003', 'match_value': 'clean-lib', 'match_basis': 'package', 'match_confidence': 0.80},
    ]
    for l in links:
        session.add(VulnLink(**l))

    session.commit()

    app = FastAPI()
    app.add_api_route('/api/cve-risk-tiers', lambda: get_cve_risk_tiers(session), methods=['GET'])

    that_app = app
    that_app.dependency_overrides[get_session] = lambda: session

    client = TestClient(that_app)
    resp = client.get('/api/cve-risk-tiers')
    assert resp.status_code == 200, f'Expected 200, got {resp.status_code}'

    data = resp.json()
    servers = data.get('servers', [])
    assert len(servers) == 2, f'Expected 2 servers, got {len(servers)}'

    valid_severities = ['HIGH', 'CRITICAL']
    for s in servers:
        assert s['severity'] in valid_severities, f"Expected severity in {valid_severities}, got {s['severity']}"

    print('PASS')