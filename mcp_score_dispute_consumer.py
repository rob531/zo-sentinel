import json
import time
from datetime import datetime
from typing import Dict, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPScoreDispute, MCPLLMAxisScore

def get_latest_axis_scores(session: Session, server_id: int) -> Dict[str, float]:
    """Retrieve the latest axis scores for a given server_id."""
    scores = session.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id
    ).order_by(MCPLLMAxisScore.created_at.desc()).first()

    if not scores:
        return {}

    return json.loads(scores.axes_scores)

def calculate_overall_risk(axis_scores: Dict[str, float]) -> float:
    """Calculate the overall risk score based on axis scores."""
    weights = {
        "toxicity": 0.3,
        "hate_speech": 0.25,
        "violence": 0.2,
        "harassment": 0.15,
        "sexual_content": 0.1
    }

    weighted_sum = sum(
        axis_scores.get(axis, 0) * weights.get(axis, 0)
        for axis in weights
    )

    return weighted_sum

def process_dispute(dispute_id: int, session: Session = Depends(get_session)) -> Dict:
    """Process a dispute by comparing proposed scores with latest axis scores."""
    dispute = session.query(MCPScoreDispute).filter(
        MCPScoreDispute.id == dispute_id
    ).first()

    if not dispute:
        return {
            "dispute_id": dispute_id,
            "resolved": False,
            "resolution": "dispute_not_found",
            "updated_at": datetime.utcnow().isoformat()
        }

    latest_scores = get_latest_axis_scores(session, dispute.server_id)
    if not latest_scores:
        return {
            "dispute_id": dispute_id,
            "resolved": False,
            "resolution": "no_latest_scores",
            "updated_at": datetime.utcnow().isoformat()
        }

    proposed_scores = json.loads(dispute.proposed_axes)
    current_overall_risk = calculate_overall_risk(latest_scores)
    proposed_overall_risk = calculate_overall_risk(proposed_scores)

    if abs(current_overall_risk - proposed_overall_risk) < 0.1:
        dispute.resolved = True
        dispute.resolution = "auto_resolved"
        dispute.updated_at = datetime.utcnow()
        session.commit()
        return {
            "dispute_id": dispute_id,
            "resolved": True,
            "resolution": "auto_resolved",
            "updated_at": dispute.updated_at.isoformat()
        }
    else:
        dispute.resolved = False
        dispute.resolution = "manual_review"
        dispute.updated_at = datetime.utcnow()
        session.commit()
        return {
            "dispute_id": dispute_id,
            "resolved": False,
            "resolution": "manual_review",
            "updated_at": dispute.updated_at.isoformat()
        }

def run():
    """Daemon entry point that polls unprocessed disputes every 30 seconds."""
    while True:
        session = get_session()
        try:
            unprocessed_disputes = session.query(MCPScoreDispute).filter(
                MCPScoreDispute.resolved == False
            ).all()

            for dispute in unprocessed_disputes:
                process_dispute(dispute.id, session)

            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Error processing disputes: {e}")
        finally:
            session.close()

        time.sleep(30)

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create mock dispute record
    mock_dispute = MCPScoreDispute(
        server_id=1,
        proposed_overall_risk=0.5,
        proposed_axes=json.dumps({
            "toxicity": 0.2,
            "hate_speech": 0.1,
            "violence": 0.1,
            "harassment": 0.05,
            "sexual_content": 0.05
        }),
        resolved=False,
        resolution="",
        updated_at=datetime.utcnow()
    )

    session = SessionLocal()
    session.add(mock_dispute)
    session.commit()

    # Create mock axis scores
    mock_scores = MCPLLMAxisScore(
        server_id=1,
        axes_scores=json.dumps({
            "toxicity": 0.21,
            "hate_speech": 0.11,
            "violence": 0.11,
            "harassment": 0.06,
            "sexual_content": 0.06
        }),
        created_at=datetime.utcnow()
    )

    session.add(mock_scores)
    session.commit()

    # Process the mock dispute
    result = process_dispute(mock_dispute.id, session)

    # Assert the result
    assert result["resolved"] is True
    assert result["resolution"] == "auto_resolved"

    print("PASS")