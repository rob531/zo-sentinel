from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
import requests
from app.db import get_session
from app.models import ServiceHealth

router = APIRouter()

def get_write_service_health() -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT service, status, meta->>'last_heartbeat' FROM service_health"
            }
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/service_health", response_model=List[Dict[str, Any]])
async def get_service_health() -> List[Dict[str, Any]]:
    return get_write_service_health()

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/service_health")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    for item in data:
        assert isinstance(item, dict)
        assert 'service' in item
        assert 'status' in item
        assert 'last_heartbeat' in item

    print("PASS")