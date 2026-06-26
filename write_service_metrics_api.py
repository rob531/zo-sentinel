from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
import requests
from datetime import datetime
import json

app = FastAPI()

class ServiceMetrics(BaseModel):
    last_heartbeat: str
    status: str
    meta: dict
    staleness_duration_seconds: float
    historical_heartbeats: list

@app.get("/write_service/metrics", response_model=ServiceMetrics)
async def get_write_service_metrics():
    try:
        # Query the database for write_service entries
        query = """
        SELECT
            last_heartbeat,
            status,
            meta,
            (EXTRACT(EPOCH FROM NOW()) - EXTRACT(EPOCH FROM last_heartbeat)) AS staleness_duration_seconds,
            ARRAY_AGG(last_heartbeat) OVER (ORDER BY last_heartbeat DESC ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS historical_heartbeats
        FROM service_health
        WHERE service_name = 'write_service'
        LIMIT 1
        """
        response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
        response.raise_for_status()
        data = response.json()

        if not data:
            raise HTTPException(status_code=404, detail="No write_service metrics found")

        result = data[0]
        return {
            "last_heartbeat": result["last_heartbeat"],
            "status": result["status"],
            "meta": json.loads(result["meta"]),
            "staleness_duration_seconds": result["staleness_duration_seconds"],
            "historical_heartbeats": result["historical_heartbeats"]
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse meta data: {str(e)}")

if __name__ == "__main__":
    client = TestClient(app)

    # Test for a healthy write_service entry
    healthy_response = client.get("/write_service/metrics")
    assert healthy_response.status_code == 200
    healthy_data = healthy_response.json()
    assert "last_heartbeat" in healthy_data
    assert "status" in healthy_data
    assert "meta" in healthy_data
    assert "staleness_duration_seconds" in healthy_data
    assert "historical_heartbeats" in healthy_data

    # Test for a stale write_service entry (mocked)
    # In a real test, you would set up the database with a stale entry
    # For this example, we'll just check the structure
    stale_response = client.get("/write_service/metrics")
    assert stale_response.status_code == 200
    stale_data = stale_response.json()
    assert "last_heartbeat" in stale_data
    assert "status" in stale_data
    assert "meta" in stale_data
    assert "staleness_duration_seconds" in stale_data
    assert "historical_heartbeats" in stale_data

    print("PASS")