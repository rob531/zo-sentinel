import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
from uuid import UUID

import requests
from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

def compute_and_store_tier(server_id: str) -> None:
    session = Depends(get_session)()

    try:
        # Fetch all axis scores for the given server
        axis_scores = session.query(MCPLLMAxisScores).filter(
            MCPLLMAxisScores.server_id == server_id
        ).all()

        if not axis_scores:
            return

        # Calculate overall risk tier
        tier = calculate_risk_tier(axis_scores)

        # Prepare payload for DB update
        payload = {
            'table': 'mcp_server_registry',
            'rows': {
                'server_id': server_id,
                'risk_tier': tier,
                'last_assessed': datetime.now(timezone.utc).isoformat()
            },
            'wait': True
        }

        # Write to database with exponential backoff
        write_with_backoff(payload)

    finally:
        session.close()

def calculate_risk_tier(axis_scores: List[MCPLLMAxisScores]) -> str:
    # Initialize counters for each risk level
    high_risk_count = 0
    medium_risk_count = 0
    low_risk_count = 0

    # Count how many axes fall into each risk category
    for score in axis_scores:
        if score.p_danger > 0.5:
            high_risk_count += 1
        elif score.p_critical > 0.5:
            medium_risk_count += 1
        else:
            low_risk_count += 1

    # Determine overall risk tier based on PRODUCT_SPEC §2
    if high_risk_count >= 4:
        return 'HIGH_RISK_ISOLATED'
    elif high_risk_count >= 2:
        return 'HIGH_RISK_MONITORED'
    elif high_risk_count == 1:
        return 'MEDIUM_RISK'
    elif medium_risk_count >= 3:
        return 'MEDIUM_RISK'
    else:
        return 'LOW_RISK'

def write_with_backoff(payload: Dict, max_retries: int = 3) -> None:
    base_delay = 0.1
    url = "http://127.0.0.1:8772/write"

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)

def mock_axis_scores(server_id: str) -> List[MCPLLMAxisScores]:
    return [
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis1",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis2",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis3",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis4",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis5",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis6",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        ),
        MCPLLMAxisScores(
            server_id=server_id,
            axis_name="axis7",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.8
        )
    ]

def mock_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def mock_requests_post(*args, **kwargs):
    captured_payload = None

    def inner(*args, **kwargs):
        nonlocal captured_payload
        captured_payload = kwargs.get('json')
        return requests.Response()

    return inner

if __name__ == "__main__":
    from app.dependency_overrides import dependency_overrides
    from app.db import get_session

    # Setup mock session
    mock_session = mock_db_session()
    dependency_overrides[get_session] = lambda: mock_session

    # Setup mock requests.post
    original_post = requests.post
    requests.post = mock_requests_post()

    try:
        # Create mock server
        server_id = str(UUID(int=1))
        mock_server = MCPServerRegistry(server_id=server_id)
        mock_session.add(mock_server)

        # Add mock axis scores
        mock_scores = mock_axis_scores(server_id)
        mock_session.add_all(mock_scores)
        mock_session.commit()

        # Call function under test
        compute_and_store_tier(server_id)

        # Verify the result
        captured_payload = requests.post.json
        assert captured_payload['rows']['risk_tier'] == 'HIGH_RISK_ISOLATED'
        print("PASS")

    finally:
        # Cleanup
        mock_session.close()
        requests.post = original_post
        dependency_overrides.clear()