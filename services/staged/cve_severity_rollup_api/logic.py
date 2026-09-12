from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

def get_cve_severity_distribution(days: int, session: Session = Depends(get_session)) -> List[dict]:
    """Count CVEs by severity over the last N days.

    Args:
        days: Number of days to look back.
        session: SQLAlchemy session.

    Returns:
        List of dictionaries with date, severity, and count.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Subquery to get advisory_ids with links
    linked_advisories = (
        select(VulnLink.advisory_id)
        .group_by(VulnLink.advisory_id)
        .having(func.count(VulnLink.id) > 0)
    ).subquery()

    # Main query joining advisories with links and filtering by date
    query = (
        select(
            func.date(VulnAdvisory.published_at).label('date'),
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label('count')
        )
        .join(linked_advisories, VulnAdvisory.id == linked_advisories.c.advisory_id)
        .where(VulnAdvisory.published_at >= cutoff_date)
        .group_by(func.date(VulnAdvisory.published_at), VulnAdvisory.severity)
        .order_by(func.date(VulnAdvisory.published_at).desc(), VulnAdvisory.severity.desc())
    )

    results = session.execute(query).fetchall()

    return [
        {
            'date': str(result.date),
            'severity': result.severity,
            'count': result.count
        }
        for result in results
    ]

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Test app
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Advisory 1 - High severity, published yesterday
        adv1 = VulnAdvisory(
            affected_ranges='[]',
            aliases='[]',
            content_hash='hash1',
            ecosystem='test',
            feed='test',
            fetched_at=datetime.utcnow(),
            id='adv1',
            identities='[]',
            package='test-package',
            published_at=datetime.utcnow() - timedelta(days=1),
            severity='High',
            source_url='http://example.com/1',
            summary='Test advisory 1'
        )
        session.add(adv1)
        session.add(VulnLink(advisory_id='adv1', server_id='server1', linked_at=datetime.utcnow()))

        # Advisory 2 - Medium severity, published yesterday
        adv2 = VulnAdvisory(
            affected_ranges='[]',
            aliases='[]',
            content_hash='hash2',
            ecosystem='test',
            feed='test',
            fetched_at=datetime.utcnow(),
            id='adv2',
            identities='[]',
            package='test-package',
            published_at=datetime.utcnow() - timedelta(days=1),
            severity='Medium',
            source_url='http://example.com/2',
            summary='Test advisory 2'
        )
        session.add(adv2)
        session.add(VulnLink(advisory_id='adv2', server_id='server1', linked_at=datetime.utcnow()))

        # Advisory 3 - High severity, published 2 days ago (should be excluded for days=1)
        adv3 = VulnAdvisory(
            affected_ranges='[]',
            aliases='[]',
            content_hash='hash3',
            ecosystem='test',
            feed='test',
            fetched_at=datetime.utcnow(),
            id='adv3',
            identities='[]',
            package='test-package',
            published_at=datetime.utcnow() - timedelta(days=2),
            severity='High',
            source_url='http://example.com/3',
            summary='Test advisory 3'
        )
        session.add(adv3)
        session.add(VulnLink(advisory_id='adv3', server_id='server1', linked_at=datetime.utcnow()))

        session.commit()
    finally:
        session.close()

    # Test the function
    client = TestClient(test_app)
    response = client.get('/api/cve/rollup?days=1')
    assert response.status_code == 200
    data = response.json()

    assert len(data['series']) == 2
    assert any(item['severity'] == 'High' and item['count'] == 1 for item in data['series'])
    assert any(item['severity'] == 'Medium' and item['count'] == 1 for item in data['series'])

    print("PASS")