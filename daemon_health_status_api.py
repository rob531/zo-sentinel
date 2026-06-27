from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from datetime import datetime
from typing import List, Dict, Any
import json

app = FastAPI()

class DaemonHealth(BaseModel):
    name: str
    last_heartbeat: str
    status: str
    meta: Dict[str, Any]

def query_daemon_health() -> List[DaemonHealth]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT name, last_heartbeat, status, meta FROM service_health"
            }
        )
        response.raise_for_status()
        data = response.json()
        return [
            DaemonHealth(
                name=row["name"],
                last_heartbeat=row["last_heartbeat"],
                status=row["status"],
                meta=json.loads(row["meta"]) if row["meta"] else {}
            )
            for row in data["data"]
        ]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/daemon_health_status", response_model=List[DaemonHealth])
async def get_daemon_health_status():
    return query_daemon_health()

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/api/daemon_health_status")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert any(daemon["status"] == "ok" for daemon in response.json())
    print("PASS")