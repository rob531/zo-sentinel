import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class WedgeEvent(BaseModel):
    host: str
    timestamp: datetime
    wasted_usd: float

class WeeklyWedgeReport(BaseModel):
    week_start: datetime
    wedge_events: List[WedgeEvent]
    total_wasted_usd: float
    blocklisted_hosts: List[str]
    top_offender_host: Optional[str]

def get_wedged_hosts() -> List[str]:
    """Mock function to get wedged hosts for testing"""
    return ["host1.example.com", "host2.example.com"]

def get_vast_ledger() -> List[Dict]:
    """Mock function to get vast ledger for testing"""
    return [
        {"host": "host1.example.com", "timestamp": "2026-07-17T12:00:00", "wasted_usd": 0.50},
        {"host": "host2.example.com", "timestamp": "2026-07-17T13:00:00", "wasted_usd": 0.40},
        {"host": "host3.example.com", "timestamp": "2026-07-17T14:00:00", "wasted_usd": 0.10}
    ]

def calculate_weekly_report(
    db: Session = Depends(get_session),
    wedged_hosts: List[str] = Depends(get_wedged_hosts),
    vast_ledger: List[Dict] = Depends(get_vast_ledger)
) -> WeeklyWedgeReport:
    """Calculate weekly wedge report from vast ledger and wedged hosts"""
    # Get current week start (Monday)
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())

    # Filter events for current week
    week_events = [
        event for event in vast_ledger
        if datetime.fromisoformat(event["timestamp"]) >= week_start
    ]

    # Filter events for wedged hosts
    wedge_events = [
        WedgeEvent(
            host=event["host"],
            timestamp=datetime.fromisoformat(event["timestamp"]),
            wasted_usd=event["wasted_usd"]
        )
        for event in week_events
        if event["host"] in wedged_hosts
    ]

    # Calculate total wasted USD
    total_wasted = sum(event.wasted_usd for event in wedge_events)

    # Get top offender host
    host_totals = {}
    for event in wedge_events:
        host_totals[event.host] = host_totals.get(event.host, 0) + event.wasted_usd
    top_offender = max(host_totals.items(), key=lambda x: x[1])[0] if host_totals else None

    return WeeklyWedgeReport(
        week_start=week_start,
        wedge_events=wedge_events,
        total_wasted_usd=total_wasted,
        blocklisted_hosts=wedged_hosts,
        top_offender_host=top_offender
    )

@router.get("/wedge-report", response_model=WeeklyWedgeReport)
async def get_wedge_report(
    db: Session = Depends(get_session),
    wedged_hosts: List[str] = Depends(get_wedged_hosts),
    vast_ledger: List[Dict] = Depends(get_vast_ledger)
):
    """Endpoint to get weekly wedge report"""
    return calculate_weekly_report(db, wedged_hosts, vast_ledger)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Override dependencies for testing
    def mock_get_wedged_hosts() -> List[str]:
        return ["host1.example.com", "host2.example.com"]

    def mock_get_vast_ledger() -> List[Dict]:
        return [
            {"host": "host1.example.com", "timestamp": "2026-07-17T12:00:00", "wasted_usd": 0.50},
            {"host": "host2.example.com", "timestamp": "2026-07-17T13:00:00", "wasted_usd": 0.40},
            {"host": "host3.example.com", "timestamp": "2026-07-17T14:00:00", "wasted_usd": 0.10}
        ]

    # Create test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/wedge-report")
    assert response.status_code == 200
    report = response.json()

    # Verify the report structure
    assert "week_start" in report
    assert "wedge_events" in report
    assert "total_wasted_usd" in report
    assert "blocklisted_hosts" in report
    assert "top_offender_host" in report

    # Verify the data
    assert len(report["wedge_events"]) == 2
    assert report["total_wasted_usd"] == 0.90
    assert len(report["blocklisted_hosts"]) == 2
    assert report["top_offender_host"] == "host1.example.com"

    print("PASS")