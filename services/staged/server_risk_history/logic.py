from typing import List, Dict, Any
from datetime import datetime, timedelta
from fastapi import Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
from sqlalchemy import func

def get_server_risk_history(server_id: str, days: int = 30) -> List[Dict[str, Any]]:
    session = Depends(get_session)

    # Get the base date for the query (days days ago from today)
    start_date = datetime.utcnow() - timedelta(days=days)

    # Query McpServerRegistry for risk tier changes
    risk_tier_changes = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed
    ).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.last_assessed >= start_date
    ).order_by(
        McpServerRegistry.last_assessed.asc()
    ).all()

    # Query McpLlmAxisScore for overall risk scores
    risk_scores = session.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.overall_risk
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date
    ).order_by(
        McpLlmAxisScore.scored_at.asc()
    ).all()

    # Combine the data into a chronological history
    history = []

    # Create a dictionary to hold the risk scores by date
    risk_scores_by_date = {}
    for score in risk_scores:
        date_str = score.scored_at.strftime('%Y-%m-%d')
        risk_scores_by_date[date_str] = score.overall_risk

    # Process risk tier changes
    for change in risk_tier_changes:
        date_str = change.last_assessed.strftime('%Y-%m-%d')
        overall_risk = risk_scores_by_date.get(date_str, None)

        history.append({
            'date': date_str,
            'risk_tier': change.risk_tier,
            'overall_risk': overall_risk
        })

    # Fill in any missing dates with the last known risk tier and score
    if history:
        current_date = start_date
        last_tier = history[0]['risk_tier']
        last_score = history[0]['overall_risk']

        while current_date <= datetime.utcnow():
            date_str = current_date.strftime('%Y-%m-%d')

            # Check if we already have an entry for this date
            existing_entry = next((h for h in history if h['date'] == date_str), None)
            if existing_entry:
                last_tier = existing_entry['risk_tier']
                last_score = existing_entry['overall_risk']
            else:
                # Add a new entry with the last known values
                history.append({
                    'date': date_str,
                    'risk_tier': last_tier,
                    'overall_risk': last_score
                })

            current_date += timedelta(days=1)

        # Sort the history by date
        history.sort(key=lambda x: x['date'])

    return history

if __name__ == '__main__':
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScore
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker

    # Create a test app and override the session dependency
    app = FastAPI()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create tables
    Base.metadata.create_all(engine)

    # Insert test data
    test_session = SessionLocal()
    test_session.add_all([
        McpServerRegistry(
            server_id="test1",
            risk_tier="low",
            last_assessed=datetime.utcnow() - timedelta(days=5)
        ),
        McpServerRegistry(
            server_id="test1",
            risk_tier="medium",
            last_assessed=datetime.utcnow() - timedelta(days=3)
        ),
        McpServerRegistry(
            server_id="test1",
            risk_tier="high",
            last_assessed=datetime.utcnow() - timedelta(days=1)
        ),
        McpLlmAxisScore(
            server_id="test1",
            scored_at=datetime.utcnow() - timedelta(days=5),
            overall_risk=0.2
        ),
        McpLlmAxisScore(
            server_id="test1",
            scored_at=datetime.utcnow() - timedelta(days=3),
            overall_risk=0.5
        ),
        McpLlmAxisScore(
            server_id="test1",
            scored_at=datetime.utcnow() - timedelta(days=1),
            overall_risk=0.8
        )
    ])
    test_session.commit()

    # Test the function
    history = get_server_risk_history("test1", days=7)
    assert len(history) == 7
    assert history[0]['risk_tier'] == 'low'
    assert history[0]['overall_risk'] == 0.2
    assert history[-1]['risk_tier'] == 'high'
    assert history[-1]['overall_risk'] == 0.8

    print("PASS")