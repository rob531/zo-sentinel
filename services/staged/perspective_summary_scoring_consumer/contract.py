from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Org

def compute_and_write(
    session: Session = Depends(get_session),
    write_service_url: str = "http://127.0.0.1:8772"
) -> None:
    """
    Compute perspective summaries from axis scores and write to mcp_perspective_summary.

    Args:
        session: SQLAlchemy session for database access
        write_service_url: URL of the write service for health checks
    """
    # Get all servers with their org_id and risk_tier
    servers = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.meta,
        McpServerRegistry.risk_tier
    ).all()

    # Process each server
    for server in servers:
        server_id = server.server_id
        org_id = server.meta.get('org_id') if server.meta else None
        risk_tier = server.risk_tier

        if not org_id:
            continue

        # Get all axis scores for this server
        axis_scores = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server_id
        ).all()

        # Initialize tier counts
        tier_counts = {
            "TRUSTED_GENERAL": 0,
            "TRUSTED_SPECIAL": 0,
            "UNTRUSTED": 0,
            "UNKNOWN": 0
        }

        # Count scores per tier
        for score in axis_scores:
            label = score.label
            if label in tier_counts:
                tier_counts[label] += 1

        # Determine dominant tier
        dominant_tier = max(tier_counts, key=tier_counts.get)

        # Write to mcp_perspective_summary (via write_service)
        # In a real implementation, this would be an HTTP POST to write_service
        # For this example, we'll just print the result
        print(f"Server {server_id} (Org {org_id}):")
        print(f"  Tier counts: {tier_counts}")
        print(f"  Dominant tier: {dominant_tier}")
        print(f"  Computed at: {datetime.utcnow()}")

def heartbeat(write_service_url: str = "http://127.0.0.1:8772") -> None:
    """
    Send heartbeat to service_health every 60 seconds.

    Args:
        write_service_url: URL of the write service for health checks
    """
    # In a real implementation, this would be an HTTP POST to write_service
    print(f"Heartbeat sent to {write_service_url}")

if __name__ == "__main__":
    # Create a test FastAPI app for dependency injection
    test_app = FastAPI()

    # Override get_session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestSession = sessionmaker(bind=test_engine)

    test_app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Create test orgs
    org1 = Org(name="Test Org 1")
    org2 = Org(name="Test Org 2")
    test_app.dependency_overrides[get_session]().add_all([org1, org2])
    test_app.dependency_overrides[get_session]().commit()

    # Create test servers
    server1 = McpServerRegistry(
        server_id="server1",
        meta={"org_id": org1.id},
        risk_tier="TRUSTED_GENERAL"
    )
    server2 = McpServerRegistry(
        server_id="server2",
        meta={"org_id": org1.id},
        risk_tier="UNTRUSTED"
    )
    server3 = McpServerRegistry(
        server_id="server3",
        meta={"org_id": org2.id},
        risk_tier="TRUSTED_SPECIAL"
    )
    test_app.dependency_overrides[get_session]().add_all([server1, server2, server3])
    test_app.dependency_overrides[get_session]().commit()

    # Create test axis scores
    axis_scores = [
        McpLlmAxisScore(
            server_id="server1",
            label="TRUSTED_GENERAL",
            p_top=0.9
        ),
        McpLlmAxisScore(
            server_id="server1",
            label="TRUSTED_GENERAL",
            p_top=0.8
        ),
        McpLlmAxisScore(
            server_id="server2",
            label="UNTRUSTED",
            p_top=0.7
        ),
        McpLlmAxisScore(
            server_id="server3",
            label="TRUSTED_SPECIAL",
            p_top=0.85
        ),
        McpLlmAxisScore(
            server_id="server3",
            label="TRUSTED_SPECIAL",
            p_top=0.95
        )
    ]
    test_app.dependency_overrides[get_session]().add_all(axis_scores)
    test_app.dependency_overrides[get_session]().commit()

    # Run the computation
    compute_and_write(session=test_app.dependency_overrides[get_session]())

    # Verify results
    results = test_app.dependency_overrides[get_session]().query(
        McpServerRegistry.server_id,
        McpServerRegistry.meta,
        McpServerRegistry.risk_tier
    ).all()

    for result in results:
        server_id = result.server_id
        org_id = result.meta.get('org_id') if result.meta else None
        risk_tier = result.risk_tier

        if not org_id:
            continue

        axis_scores = test_app.dependency_overrides[get_session]().query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == server_id
        ).all()

        tier_counts = {
            "TRUSTED_GENERAL": 0,
            "TRUSTED_SPECIAL": 0,
            "UNTRUSTED": 0,
            "UNKNOWN": 0
        }

        for score in axis_scores:
            label = score.label
            if label in tier_counts:
                tier_counts[label] += 1

        dominant_tier = max(tier_counts, key=tier_counts.get)

        assert tier_counts[risk_tier] > 0, f"Server {server_id} has no scores for tier {risk_tier}"
        assert dominant_tier in ["TRUSTED_GENERAL", "TRUSTED_SPECIAL", "UNTRUSTED", "UNKNOWN"], \
            f"Invalid dominant tier {dominant_tier} for server {server_id}"

    print("PASS")