from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import Depends, HTTPException
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

def get_risk_tier_distribution(days: int, session: Session = Depends(get_session)) -> Dict:
    """Get risk tier distribution for the past N days.

    Args:
        days: Number of days to look back.
        session: SQLAlchemy session.

    Returns:
        Dictionary with days and distribution data.
    """
    cutoff_date = datetime.now() - timedelta(days=days)

    query = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label('day'),
            McpServerRegistry.risk_tier,
            func.count(McpLlmAxisScore.server_id.distinct()).label('count')
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff_date)
        .group_by('day', McpServerRegistry.risk_tier)
        .order_by('day')
        .all()
    )

    distribution = []
    for day, tier, count in query:
        found = False
        for entry in distribution:
            if entry['date'] == day.strftime('%Y-%m-%d'):
                entry['tier_counts'][tier] = count
                found = True
                break
        if not found:
            distribution.append({
                'date': day.strftime('%Y-%m-%d'),
                'tier_counts': {tier: count}
            })

    return {
        'days': days,
        'distribution': distribution
    }

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    test_data = [
        (1, 'TRUSTED_GENERAL', '2023-01-01', 1),
        (2, 'TRUSTED_RESEARCH', '2023-01-01', 1),
        (3, 'TRUSTED_GENERAL', '2023-01-02', 1),
        (4, 'UNTRUSTED', '2023-01-02', 1),
    ]

    for server_id, tier, date, _ in test_data:
        session.add(McpServerRegistry(
            server_id=server_id,
            risk_tier=tier,
            last_seen=datetime.strptime(date, '%Y-%m-%d')
        ))
        session.add(McpLlmAxisScore(
            server_id=server_id,
            axis_name='test_axis',
            p_top=0.5,
            scored_at=datetime.strptime(date, '%Y-%m-%d')
        ))
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get('/api/risk/tier_distribution?days=2')
    assert response.status_code == 200
    data = response.json()

    assert data['days'] == 2
    assert len(data['distribution']) == 2

    # Verify counts for each day
    for entry in data['distribution']:
        if entry['date'] == '2023-01-01':
            assert entry['tier_counts'] == {'TRUSTED_GENERAL': 1, 'TRUSTED_RESEARCH': 1}
        elif entry['date'] == '2023-01-02':
            assert entry['tier_counts'] == {'TRUSTED_GENERAL': 1, 'UNTRUSTED': 1}

    print("PASS")