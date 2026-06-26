from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
import requests
from typing import List, Dict, Any

router = APIRouter()

def query_write_service(query: str) -> List[Dict[str, Any]]:
    """Helper function to query the write_service via HTTP POST."""
    try:
        response = requests.post(
            "http://write_service:8000/query",
            json={"query": query},
            timeout=5
        )
        response.raise_for_status()
        return response.json()["data"]
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to query write_service: {str(e)}")

def get_daemon_heartbeats() -> List[Dict[str, Any]]:
    """Query service_health table for daemon statuses."""
    query = """
    SELECT
        daemon_name,
        last_heartbeat,
        TIMESTAMPDIFF('second', last_heartbeat, CURRENT_TIMESTAMP) AS age_seconds
    FROM service_health
    WHERE daemon_name IN ('write_service', 'mcp_daemon', 'signal_daemon')
    """
    results = query_write_service(query)
    return [
        {
            "name": f"daemon/{row['daemon_name']}",
            "last_updated": row["last_heartbeat"],
            "age_seconds": row["age_seconds"],
            "status": get_freshness_status(row["age_seconds"])
        }
        for row in results
    ]

def get_table_freshness() -> List[Dict[str, Any]]:
    """Query information_schema for table last update times."""
    tables = ["mcp_server_registry", "mcp_signal_scores", "mcp_alerts"]
    results = []

    for table in tables:
        # Use direct query to get last update time for each table
        query = f"""
        SELECT
            '{table}' AS table_name,
            MAX(created_at) AS last_updated
        FROM {table}
        """
        rows = query_write_service(query)
        if rows:
            row = rows[0]
            age_seconds = (datetime.now() - datetime.fromisoformat(row["last_updated"])).total_seconds()
            results.append({
                "name": f"table/{table}",
                "last_updated": row["last_updated"],
                "age_seconds": int(age_seconds),
                "status": get_freshness_status(int(age_seconds))
            })

    return results

def get_freshness_status(age_seconds: int) -> str:
    """Determine freshness status based on age in seconds."""
    if age_seconds < 3600:  # Less than 1 hour
        return "fresh"
    elif age_seconds < 86400:  # Less than 24 hours
        return "stale"
    else:
        return "critical"

@router.get("/data_freshness")
async def data_freshness() -> Dict[str, List[Dict[str, Any]]]:
    """Endpoint to get data freshness metrics."""
    try:
        daemon_data = get_daemon_heartbeats()
        table_data = get_table_freshness()
        combined_data = daemon_data + table_data
        return {"data_sources": combined_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching data freshness: {str(e)}")

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/data_freshness")
    assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"

    data = response.json()
    assert "data_sources" in data, "Missing 'data_sources' key in response"
    assert isinstance(data["data_sources"], list), "'data_sources' should be a list"

    expected_sources = [
        "daemon/write_service",
        "daemon/mcp_daemon",
        "daemon/signal_daemon",
        "table/mcp_server_registry",
        "table/mcp_signal_scores",
        "table/mcp_alerts"
    ]

    actual_sources = [item["name"] for item in data["data_sources"]]
    for source in expected_sources:
        assert source in actual_sources, f"Missing expected source: {source}"

    for item in data["data_sources"]:
        assert "last_updated" in item, "Missing 'last_updated' key"
        assert isinstance(item["last_updated"], str), "'last_updated' should be a string"
        assert "age_seconds" in item, "Missing 'age_seconds' key"
        assert isinstance(item["age_seconds"], int), "'age_seconds' should be an integer"
        assert "status" in item, "Missing 'status' key"
        assert item["status"] in ["fresh", "stale", "critical"], "Invalid status value"

    print("PASS")