from datetime import datetime
from typing import List, Dict, Any
import json
import requests
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

def aggregate_daily_tiers(date: str) -> int:
    session = get_session()
    try:
        # Get all servers with their current risk tier
        servers = session.query(McpServerRegistry).all()
        server_tiers = {server.id: server.risk_tier for server in servers}

        # Get all scores for the given date
        scores = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.scored_at >= date,
            McpLlmAxisScore.scored_at < f"{date}T23:59:59"
        ).all()

        # Calculate new risk tiers based on scores
        tier_counts = {}
        for server_id in server_tiers:
            server_scores = [score for score in scores if score.server_id == server_id]
            if not server_scores:
                continue

            # Simple tier calculation logic (adjust as needed)
            avg_p_top = sum(score.p_top for score in server_scores) / len(server_scores)
            if avg_p_top < 0.3:
                new_tier = "LOW"
            elif avg_p_top < 0.6:
                new_tier = "MEDIUM"
            else:
                new_tier = "HIGH"

            # Update tier counts
            tier_counts[new_tier] = tier_counts.get(new_tier, 0) + 1

        # Prepare data for write_service
        summary_rows = [{
            "date": date,
            "tier": tier,
            "server_count": count
        } for tier, count in tier_counts.items()]

        # Send to write_service
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json={"rows": summary_rows, "table": "mcp_risk_tier_summary"}
        )
        response.raise_for_status()

        return len(servers)

    finally:
        session.close()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    session = SessionLocal()
    try:
        # Insert test servers
        session.add_all([
            McpServerRegistry(id=1, risk_tier="MEDIUM", last_assessed="2026-07-01"),
            McpServerRegistry(id=2, risk_tier="HIGH", last_assessed="2026-07-01")
        ])

        # Insert test scores
        session.add_all([
            McpLlmAxisScore(server_id=1, axis_name="axis1", p_top=0.2, scored_at="2026-07-01T12:00:00"),
            McpLlmAxisScore(server_id=1, axis_name="axis2", p_top=0.3, scored_at="2026-07-01T12:00:00"),
            McpLlmAxisScore(server_id=2, axis_name="axis1", p_top=0.7, scored_at="2026-07-01T12:00:00"),
            McpLlmAxisScore(server_id=2, axis_name="axis2", p_top=0.8, scored_at="2026-07-01T12:00:00")
        ])
        session.commit()

        # Run aggregation
        count = aggregate_daily_tiers("2026-07-01")
        assert count == 2

        # Verify results
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_risk_tier_summary WHERE date = '2026-07-01'"}
        )
        data = response.json()
        assert len(data["rows"]) == 2
        tiers = {row["tier"] for row in data["rows"]}
        assert "LOW" in tiers and "HIGH" in tiers

        print("PASS")
    finally:
        session.close()