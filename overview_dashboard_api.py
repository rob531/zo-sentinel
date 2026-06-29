from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import requests
import json
from datetime import datetime, timedelta

app = FastAPI()

class TierDistribution(BaseModel):
    tier_distribution: Dict[str, int]
    total_servers: int
    recent_scans: List[dict]
    seven_day_trend: List[dict]

def get_mcp_server_registry(org_id: str):
    url = "http://write_service/query"
    headers = {"Content-Type": "application/json"}
    payload = {
        "table": "mcp_server_registry",
        "scope": {"org_id": org_id}
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch data from write_service")
    return response.json()

def process_data(data: List[dict]) -> TierDistribution:
    tier_distribution = {}
    total_servers = len(data)
    recent_scans = []
    seven_day_trend = []

    # Calculate tier distribution
    for server in data:
        tier = server.get("tier", "unknown")
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

    # Get recent scans (last 10 scans)
    recent_scans = sorted(data, key=lambda x: x.get("last_scan_time", ""), reverse=True)[:10]

    # Calculate 7-day trend
    today = datetime.now().date()
    seven_days_ago = today - timedelta(days=7)
    trend_data = {}

    for server in data:
        last_scan_date = datetime.strptime(server.get("last_scan_time", ""), "%Y-%m-%d").date() if server.get("last_scan_time") else None
        if last_scan_date and last_scan_date >= seven_days_ago:
            day = last_scan_date.strftime("%Y-%m-%d")
            trend_data[day] = trend_data.get(day, 0) + 1

    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        seven_day_trend.append({"date": day, "count": trend_data.get(day, 0)})

    seven_day_trend.reverse()

    return TierDistribution(
        tier_distribution=tier_distribution,
        total_servers=total_servers,
        recent_scans=recent_scans,
        seven_day_trend=seven_day_trend
    )

@app.get("/dashboard/overview", response_model=TierDistribution)
async def get_overview_dashboard(org_id: str):
    data = get_mcp_server_registry(org_id)
    return process_data(data)

if __name__ == "__main__":
    import uvicorn
    import pytest

    def test_tier_distribution():
        sample_data = [
            {"tier": "tier1", "last_scan_time": "2023-01-01"},
            {"tier": "tier2", "last_scan_time": "2023-01-02"},
            {"tier": "tier1", "last_scan_time": "2023-01-03"},
            {"tier": "tier3", "last_scan_time": "2023-01-04"},
            {"tier": "tier2", "last_scan_time": "2023-01-05"}
        ]
        result = process_data(sample_data)
        assert sum(result.tier_distribution.values()) == len(sample_data)
        print("PASS")

    test_tier_distribution()
    uvicorn.run(app, host="0.0.0.0", port=8000)