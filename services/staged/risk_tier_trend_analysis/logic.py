from typing import List, Dict, Optional
from datetime import datetime, timedelta
from fastapi import Depends
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel

class Pattern(BaseModel):
    description: str
    servers_affected: int
    start_date: str
    end_date: str

class Anomaly(BaseModel):
    description: str
    server_id: int
    date: str
    previous_tier: str
    current_tier: str

class AnalysisResult(BaseModel):
    patterns: List[Pattern]
    anomalies: List[Anomaly]

def analyze_risk_tier_trends(days: int, session: Session = Depends(get_session)) -> AnalysisResult:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get all servers with score changes in the period
    subquery = session.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.score_date,
        func.sum(McpLlmAxisScore.score).label('total_score')
    ).filter(
        McpLlmAxisScore.score_date >= start_date,
        McpLlmAxisScore.score_date <= end_date
    ).group_by(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.score_date
    ).subquery()

    # Join with server registry to get risk tiers
    query = session.query(
        subquery.c.server_id,
        subquery.c.score_date,
        subquery.c.total_score,
        McpServerRegistry.risk_tier
    ).join(
        McpServerRegistry,
        McpServerRegistry.id == subquery.c.server_id
    ).order_by(
        subquery.c.server_id,
        subquery.c.score_date
    )

    results = query.all()

    # Process results to find patterns and anomalies
    patterns = []
    anomalies = []

    # Track tier changes per server
    server_tiers = {}
    for row in results:
        server_id = row.server_id
        date = row.score_date
        tier = row.risk_tier

        if server_id not in server_tiers:
            server_tiers[server_id] = []

        server_tiers[server_id].append((date, tier))

    # Detect patterns (e.g., multiple servers changing tiers together)
    for server_id, tiers in server_tiers.items():
        if len(tiers) < 2:
            continue

        for i in range(1, len(tiers)):
            prev_date, prev_tier = tiers[i-1]
            curr_date, curr_tier = tiers[i]

            if prev_tier != curr_tier:
                # Check if this is part of a pattern
                pattern_found = False
                for pattern in patterns:
                    if (pattern.description == f"Tier change from {prev_tier} to {curr_tier}" and
                        (curr_date - prev_date).days <= 3):
                        pattern.servers_affected += 1
                        pattern.end_date = curr_date.strftime('%Y-%m-%d')
                        pattern_found = True
                        break

                if not pattern_found:
                    patterns.append(Pattern(
                        description=f"Tier change from {prev_tier} to {curr_tier}",
                        servers_affected=1,
                        start_date=prev_date.strftime('%Y-%m-%d'),
                        end_date=curr_date.strftime('%Y-%m-%d')
                    ))

                # Add anomaly
                anomalies.append(Anomaly(
                    description=f"Unexpected tier change from {prev_tier} to {curr_tier}",
                    server_id=server_id,
                    date=curr_date.strftime('%Y-%m-%d'),
                    previous_tier=prev_tier,
                    current_tier=curr_tier
                ))

    return AnalysisResult(patterns=patterns, anomalies=anomalies)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        servers = [
            McpServerRegistry(id=1, hostname="server1", risk_tier="low"),
            McpServerRegistry(id=2, hostname="server2", risk_tier="medium"),
            McpServerRegistry(id=3, hostname="server3", risk_tier="high"),
            McpServerRegistry(id=4, hostname="server4", risk_tier="low"),
            McpServerRegistry(id=5, hostname="server5", risk_tier="medium")
        ]
        session.add_all(servers)

        # Create test scores
        scores = [
            # Server 1: low -> medium
            McpLlmAxisScore(server_id=1, score_date=datetime.utcnow() - timedelta(days=3), score=50),
            McpLlmAxisScore(server_id=1, score_date=datetime.utcnow() - timedelta(days=2), score=60),
            McpLlmAxisScore(server_id=1, score_date=datetime.utcnow() - timedelta(days=1), score=70),

            # Server 2: medium -> high
            McpLlmAxisScore(server_id=2, score_date=datetime.utcnow() - timedelta(days=3), score=70),
            McpLlmAxisScore(server_id=2, score_date=datetime.utcnow() - timedelta(days=2), score=80),
            McpLlmAxisScore(server_id=2, score_date=datetime.utcnow() - timedelta(days=1), score=90),

            # Server 3: high -> low (anomaly)
            McpLlmAxisScore(server_id=3, score_date=datetime.utcnow() - timedelta(days=3), score=90),
            McpLlmAxisScore(server_id=3, score_date=datetime.utcnow() - timedelta(days=2), score=80),
            McpLlmAxisScore(server_id=3, score_date=datetime.utcnow() - timedelta(days=1), score=30),

            # Server 4: no change
            McpLlmAxisScore(server_id=4, score_date=datetime.utcnow() - timedelta(days=3), score=40),
            McpLlmAxisScore(server_id=4, score_date=datetime.utcnow() - timedelta(days=2), score=45),
            McpLlmAxisScore(server_id=4, score_date=datetime.utcnow() - timedelta(days=1), score=50),

            # Server 5: medium -> low
            McpLlmAxisScore(server_id=5, score_date=datetime.utcnow() - timedelta(days=3), score=60),
            McpLlmAxisScore(server_id=5, score_date=datetime.utcnow() - timedelta(days=2), score=50),
            McpLlmAxisScore(server_id=5, score_date=datetime.utcnow() - timedelta(days=1), score=40)
        ]
        session.add_all(scores)

        # Update risk tiers
        session.query(McpServerRegistry).filter(McpServerRegistry.id == 1).update({"risk_tier": "medium"})
        session.query(McpServerRegistry).filter(McpServerRegistry.id == 2).update({"risk_tier": "high"})
        session.query(McpServerRegistry).filter(McpServerRegistry.id == 3).update({"risk_tier": "low"})
        session.query(McpServerRegistry).filter(McpServerRegistry.id == 5).update({"risk_tier": "low"})

        session.commit()

        # Run analysis
        result = analyze_risk_tier_trends(days=3)

        # Verify results
        assert len(result.patterns) > 0
        assert any(p.description == "Tier change from low to medium" for p in result.patterns)
        assert any(a.description == "Unexpected tier change from high to low" for a in result.anomalies)

        print("PASS")
    finally:
        session.close()