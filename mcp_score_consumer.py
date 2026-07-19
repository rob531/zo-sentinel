import json
import time
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPLLMAxisScore, MCPServerRegistry

def calculate_risk_tier(axis_scores: List[MCPLLMAxisScore]) -> Dict[str, str]:
    """Calculate risk tier based on axis scores."""
    high_risk_axes = sum(1 for score in axis_scores if score.escalated)
    if high_risk_axes >= 2:
        return {"risk_tier": "HIGH_RISK_ISOLATED", "verdict": "High risk due to multiple escalated axes"}
    elif high_risk_axes == 1:
        return {"risk_tier": "MODERATE_RISK_MONITORED", "verdict": "Moderate risk due to one escalated axis"}
    else:
        return {"risk_tier": "LOW_RISK", "verdict": "Low risk"}

def process_pending_scores(session: Session) -> bool:
    """Process pending scores and update server registry."""
    pending_scores = session.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.processed == False).all()
    if not pending_scores:
        return False

    server_scores: Dict[str, List[MCPLLMAxisScore]] = {}
    for score in pending_scores:
        if score.server_id not in server_scores:
            server_scores[score.server_id] = []
        server_scores[score.server_id].append(score)

    updates = []
    for server_id, scores in server_scores.items():
        risk_info = calculate_risk_tier(scores)
        avg_confidence = sum(score.p_top for score in scores) / len(scores)

        updates.append({
            "server_id": server_id,
            "risk_tier": risk_info["risk_tier"],
            "verdict": risk_info["verdict"],
            "confidence": avg_confidence,
            "last_assessed": datetime.utcnow()
        })

        for score in scores:
            score.processed = True

    session.commit()

    if updates:
        import requests
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "mcp_server_registry",
                "rows": updates,
                "wait": True
            }
        )
        if response.status_code != 200:
            raise Exception(f"Failed to update server registry: {response.text}")

    return True

def run() -> None:
    """Main consumer loop."""
    session = Depends(get_session)()
    try:
        while True:
            try:
                if not process_pending_scores(session):
                    time.sleep(1)
            except Exception as e:
                print(f"Error processing scores: {e}")
                time.sleep(5)
    except KeyboardInterrupt:
        print("Shutting down gracefully...")
    finally:
        session.close()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        return TestSession()

    app.dependency_overrides[get_session] = override_get_session

    # Create tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Insert test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        risk_tier="UNKNOWN",
        verdict="Initial state",
        confidence=0.0,
        last_assessed=datetime.utcnow()
    )
    test_session.add(test_server)

    test_score1 = MCPLLMAxisScore(
        server_id="test-server-1",
        axis_name="security",
        p_top=0.9,
        p_critical=0.8,
        p_danger=0.7,
        escalated=True,
        decision_rule_version="1.0",
        scored_at=datetime.utcnow(),
        processed=False
    )
    test_session.add(test_score1)

    test_score2 = MCPLLMAxisScore(
        server_id="test-server-1",
        axis_name="performance",
        p_top=0.8,
        p_critical=0.7,
        p_danger=0.6,
        escalated=True,
        decision_rule_version="1.0",
        scored_at=datetime.utcnow(),
        processed=False
    )
    test_session.add(test_score2)

    test_session.commit()

    # Run consumer for a short period
    import threading
    consumer_thread = threading.Thread(target=run)
    consumer_thread.start()
    time.sleep(1)
    consumer_thread.join()

    # Verify updates
    test_session = TestSession()
    updated_server = test_session.query(MCPServerRegistry).filter_by(server_id="test-server-1").first()
    if updated_server.risk_tier == "HIGH_RISK_ISOLATED":
        print("PASS")
    else:
        print(f"FAIL: Expected HIGH_RISK_ISOLATED, got {updated_server.risk_tier}")