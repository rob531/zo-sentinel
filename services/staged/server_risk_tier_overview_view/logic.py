from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

def get_server_risk_tier_overview(session: Session = Depends(get_session)):
    result = session.query(
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
        McpServerRegistry.trust_score
    ).all()

    overview = {
        'risk_tier_distribution': {},
        'verdict_distribution': {},
        'average_confidence': 0,
        'average_trust_score': 0
    }

    total_confidence = 0
    total_trust_score = 0
    count = len(result)

    for row in result:
        tier, verdict, confidence, trust_score = row

        if tier in overview['risk_tier_distribution']:
            overview['risk_tier_distribution'][tier] += 1
        else:
            overview['risk_tier_distribution'][tier] = 1

        if verdict in overview['verdict_distribution']:
            overview['verdict_distribution'][verdict] += 1
        else:
            overview['verdict_distribution'][verdict] = 1

        total_confidence += confidence
        total_trust_score += trust_score

    if count > 0:
        overview['average_confidence'] = total_confidence / count
        overview['average_trust_score'] = total_trust_score / count

    return overview

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    test_servers = [
        McpServerRegistry(
            server_id="server1",
            name="Server 1",
            risk_tier="high",
            verdict="malicious",
            confidence=0.9,
            trust_score=0.8
        ),
        McpServerRegistry(
            server_id="server2",
            name="Server 2",
            risk_tier="medium",
            verdict="suspicious",
            confidence=0.7,
            trust_score=0.6
        ),
        McpServerRegistry(
            server_id="server3",
            name="Server 3",
            risk_tier="low",
            verdict="benign",
            confidence=0.5,
            trust_score=0.4
        )
    ]

    db.add_all(test_servers)
    db.commit()

    overview = get_server_risk_tier_overview(db)

    expected_overview = {
        'risk_tier_distribution': {'high': 1, 'medium': 1, 'low': 1},
        'verdict_distribution': {'malicious': 1, 'suspicious': 1, 'benign': 1},
        'average_confidence': (0.9 + 0.7 + 0.5) / 3,
        'average_trust_score': (0.8 + 0.6 + 0.4) / 3
    }

    assert overview == expected_overview, f"Expected {expected_overview}, got {overview}"

    print("PASS")