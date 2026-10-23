from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

def get_risk_tier_summary(session: Session = Depends(get_session)):
    total_servers = session.query(func.count(McpServerRegistry.server_id)).scalar()

    tier_summary = (
        session.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label('count'),
            (func.count(McpServerRegistry.server_id) * 100.0 / total_servers).label('percentage')
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    return {
        'total_servers': total_servers,
        'tier_summary': [
            {'tier': tier, 'count': count, 'percentage': percentage}
            for tier, count, percentage in tier_summary
        ]
    }

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    test_servers = [
        McpServerRegistry(server_id='server1', risk_tier='low'),
        McpServerRegistry(server_id='server2', risk_tier='medium'),
        McpServerRegistry(server_id='server3', risk_tier='high')
    ]

    db.add_all(test_servers)
    db.commit()

    summary = get_risk_tier_summary(db)

    assert summary['total_servers'] == 3
    assert len(summary['tier_summary']) == 3
    assert summary['tier_summary'][0]['tier'] in ['low', 'medium', 'high']
    assert summary['tier_summary'][1]['tier'] in ['low', 'medium', 'high']
    assert summary['tier_summary'][2]['tier'] in ['low', 'medium', 'high']

    print('PASS')