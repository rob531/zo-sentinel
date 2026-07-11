from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPLLMAxisScore
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class AxisScoreTrendItem(BaseModel):
    timestamp: str
    axis_name: str
    mean_p_top: float
    count: int
    p_critical_pct: float

class AxisScoreTrendResponse(BaseModel):
    series: List[AxisScoreTrendItem]

@router.get("/fleet/axis-score-trend", response_model=AxisScoreTrendResponse)
async def get_axis_score_trend(
    axis_name: Optional[str] = None,
    window_days: int = 30,
    bucket_hours: int = 24,
    session=Depends(get_session)
):
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=window_days)

    query_params = {
        "table": "mcp_llm_axis_scores",
        "filters": {
            "scored_at": {
                "gte": start_time.isoformat(),
                "lte": end_time.isoformat()
            }
        }
    }

    if axis_name:
        query_params["filters"]["axis_name"] = axis_name

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=query_params
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not data:
        return {"series": []}

    time_series = {}

    for item in data:
        scored_at = datetime.fromisoformat(item["scored_at"])
        bucket_start = scored_at.replace(
            hour=scored_at.hour // bucket_hours * bucket_hours,
            minute=0,
            second=0,
            microsecond=0
        )
        bucket_key = bucket_start.isoformat()

        axis_key = item["axis_name"]

        if bucket_key not in time_series:
            time_series[bucket_key] = {}

        if axis_key not in time_series[bucket_key]:
            time_series[bucket_key][axis_key] = {
                "sum_p_top": 0,
                "count": 0,
                "critical_count": 0
            }

        time_series[bucket_key][axis_key]["sum_p_top"] += item["p_top"]
        time_series[bucket_key][axis_key]["count"] += 1

        if item["p_top"] >= 0.9:
            time_series[bucket_key][axis_key]["critical_count"] += 1

    result_series = []

    for bucket_key, axes in time_series.items():
        for axis_key, stats in axes.items():
            mean_p_top = stats["sum_p_top"] / stats["count"]
            p_critical_pct = (stats["critical_count"] / stats["count"]) * 100

            result_series.append({
                "timestamp": bucket_key,
                "axis_name": axis_key,
                "mean_p_top": round(mean_p_top, 4),
                "count": stats["count"],
                "p_critical_pct": round(p_critical_pct, 2)
            })

    return {"series": result_series}

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import MCPLLMAxisScore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    test_data = [
        {
            "id": 1,
            "axis_name": "risk",
            "p_top": 0.85,
            "scored_at": "2023-01-01T12:00:00Z"
        },
        {
            "id": 2,
            "axis_name": "risk",
            "p_top": 0.95,
            "scored_at": "2023-01-01T13:00:00Z"
        },
        {
            "id": 3,
            "axis_name": "compliance",
            "p_top": 0.75,
            "scored_at": "2023-01-01T12:00:00Z"
        },
        {
            "id": 4,
            "axis_name": "risk",
            "p_top": 0.80,
            "scored_at": "2023-01-02T12:00:00Z"
        }
    ]

    class MockSession:
        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return test_data

    app.dependency_overrides[get_session] = lambda: MockSession()

    client = TestClient(app)

    response = client.get("/fleet/axis-score-trend?window_days=2&bucket_hours=24")

    assert response.status_code == 200
    assert isinstance(response.json()["series"], list)
    assert len(response.json()["series"]) > 0

    for item in response.json()["series"]:
        assert "timestamp" in item
        assert "axis_name" in item
        assert "mean_p_top" in item
        assert "count" in item
        assert "p_critical_pct" in item

    print("PASS")