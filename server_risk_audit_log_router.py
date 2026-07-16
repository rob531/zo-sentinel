from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict
from datetime import datetime
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class AuditLogEntry(BaseModel):
    timestamp: str
    action: str
    details: Dict

def get_risk_audit(server_id: str) -> List[dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT timestamp, action, details FROM audit_log WHERE target_server_id = $1 ORDER BY timestamp DESC",
                "params": [server_id]
            }
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/servers/{server_id}/risk-audit", response_model=List[AuditLogEntry])
async def risk_audit_log(server_id: str):
    audit_entries = get_risk_audit(server_id)
    return audit_entries

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from unittest.mock import patch

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    mock_audit_entries = [
        {
            "timestamp": "2023-01-01T12:00:00Z",
            "action": "score_updated",
            "details": {"old_score": 0.5, "new_score": 0.7}
        },
        {
            "timestamp": "2023-01-02T12:00:00Z",
            "action": "dispute_created",
            "details": {"dispute_id": 123, "reason": "Incorrect data"}
        }
    ]

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_audit_entries

        response = client.get("/servers/test-server/risk-audit")

        assert response.status_code == 200
        assert response.json() == mock_audit_entries
        print("PASS")