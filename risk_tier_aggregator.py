from typing import List, Dict
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from fastapi import Depends
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

def get_risk_tier(score: float) -> str:
    if score < 0.3:
        return "low"
    elif score < 0.7:
        return "medium"
    else:
        return "high"

def compute_daily_summary(start_date: str, end_date: str) -> List[Dict]:
    db = Depends(get_session)()

    # Query to get server_id and overall_risk scores between start_date and end_date
    query = db.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.score,
        McpLlmAxisScore.created_at
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.server_id
    ).filter(
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.created_at >= start_date,
        McpLlmAxisScore.created_at <= end_date
    ).all()

    # Group by date and tier
    daily_summary = {}
    for row in query:
        date = row.created_at.date().isoformat()
        tier = get_risk_tier(row.score)

        if date not in daily_summary:
            daily_summary[date] = {}

        if tier not in daily_summary[date]:
            daily_summary[date][tier] = 0

        daily_summary[date][tier] += 1

    # Prepare the result list
    result = []
    for date, tiers in daily_summary.items():
        for tier, count in tiers.items():
            result.append({
                "date": date,
                "tier": tier,
                "server_count": count
            })

    # Write to risk_tier_daily_summary table
    write_service_url = "http://127.0.0.1:8772/write"
    import requests
    for row in result:
        requests.post(write_service_url, json={
            "table": "risk_tier_daily_summary",
            "rows": row,
            "wait": True
        })

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    db = SessionLocal()
    test_servers = [
        McpServerRegistry(server_id="server1"),
        McpServerRegistry(server_id="server2"),
        McpServerRegistry(server_id="server3")
    ]
    db.add_all(test_servers)

    test_scores = [
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            score=0.2,
            created_at=datetime.strptime("2023-01-01", "%Y-%m-%d")
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            score=0.5,
            created_at=datetime.strptime("2023-01-01", "%Y-%m-%d")
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="overall_risk",
            score=0.8,
            created_at=datetime.strptime("2023-01-01", "%Y-%m-%d")
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="overall_risk",
            score=0.3,
            created_at=datetime.strptime("2023-01-02", "%Y-%m-%d")
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="overall_risk",
            score=0.6,
            created_at=datetime.strptime("2023-01-02", "%Y-%m-%d")
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="overall_risk",
            score=0.9,
            created_at=datetime.strptime("2023-01-02", "%Y-%m-%d")
        )
    ]
    db.add_all(test_scores)
    db.commit()

    # Call the function
    result = compute_daily_summary("2023-01-01", "2023-01-02")

    # Assertions
    assert len(result) == 6
    assert result[0]["server_count"] == 1
    assert result[1]["server_count"] == 1
    assert result[2]["server_count"] == 1
    assert result[3]["server_count"] == 1
    assert result[4]["server_count"] == 1
    assert result[5]["server_count"] == 1

    print("PASS")