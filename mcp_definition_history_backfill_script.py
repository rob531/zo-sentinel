import requests
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpDefinitionHistory
from fastapi import Depends

def backfill_definition_history(session: Depends(get_session)):
    servers = session.query(McpServerRegistry).all()
    for server in servers:
        latest_score = session.query(McpLlmAxisScores).filter_by(server_id=server.id).order_by(McpLlmAxisScores.timestamp.desc()).first()
        if latest_score:
            existing_entry = session.query(McpDefinitionHistory).filter_by(server_id=server.id, timestamp=latest_score.timestamp).first()
            if not existing_entry:
                history_entry = McpDefinitionHistory(
                    server_id=server.id,
                    timestamp=latest_score.timestamp,
                    confidence=server.confidence,
                    description=server.description,
                    axis_scores=latest_score.axis_scores
                )
                session.add(history_entry)
    session.commit()

def main():
    # Self-test setup
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    test_session = Session()

    # Insert mock data
    server1 = McpServerRegistry(confidence=0.9, description="Test Server 1")
    server2 = McpServerRegistry(confidence=0.8, description="Test Server 2")
    test_session.add(server1)
    test_session.add(server2)
    test_session.commit()

    timestamp = datetime.now()
    score1 = McpLlmAxisScores(server_id=server1.id, timestamp=timestamp, axis_scores={"axis1": 0.5, "axis2": 0.6})
    score2 = McpLlmAxisScores(server_id=server2.id, timestamp=timestamp, axis_scores={"axis1": 0.4, "axis2": 0.5})
    test_session.add(score1)
    test_session.add(score2)
    test_session.commit()

    # Override the session dependency for the test
    from app.dependency_overrides import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Run the backfill
    backfill_definition_history(test_session)

    # Verify the results
    history_entries = test_session.query(McpDefinitionHistory).all()
    assert len(history_entries) == 2
    for entry in history_entries:
        assert entry.timestamp == timestamp
        if entry.server_id == server1.id:
            assert entry.confidence == 0.9
            assert entry.description == "Test Server 1"
            assert entry.axis_scores == {"axis1": 0.5, "axis2": 0.6}
        elif entry.server_id == server2.id:
            assert entry.confidence == 0.8
            assert entry.description == "Test Server 2"
            assert entry.axis_scores == {"axis1": 0.4, "axis2": 0.5}

    print("PASS")

if __name__ == '__main__':
    main()