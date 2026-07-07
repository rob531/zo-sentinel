# deps: requests, fastapi
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

app = FastAPI()

class HealthResponse(BaseModel):
    status: str
    service: str

class ServerVerdictResponse(BaseModel):
    server_id: str
    verdict: str
    risk_tier: str

class ServerAxesResponse(BaseModel):
    server_id: str
    axes: dict

class ServerSummaryResponse(BaseModel):
    server_id: str
    name: str
    verdict: str
    risk_tier: str
    last_assessed: str

class ServerListResponse(BaseModel):
    servers: List[dict]

class AxisScoreHistoryResponse(BaseModel):
    server_id: str
    history: List[dict]

def query_write_service(sql: str, params: dict = None):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params or {}},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "service": "external_api_server"}

@app.get("/servers/{server_id}/verdict", response_model=ServerVerdictResponse)
async def get_server_verdict(server_id: str):
    sql = """
        SELECT
            s.server_id,
            s.verdict,
            s.risk_tier
        FROM mcp_server_registry s
        WHERE s.server_id = :server_id
    """
    result = query_write_service(sql, {"server_id": server_id})
    if not result:
        raise HTTPException(status_code=404, detail="Server not found")
    return result[0]

@app.get("/servers/{server_id}/axes", response_model=ServerAxesResponse)
async def get_server_axes(server_id: str):
    sql = """
        SELECT
            s.server_id,
            a.axes
        FROM mcp_server_registry s
        JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE s.server_id = :server_id
    """
    result = query_write_service(sql, {"server_id": server_id})
    if not result:
        raise HTTPException(status_code=404, detail="Server not found")
    return result[0]

@app.get("/servers/{server_id}/summary", response_model=ServerSummaryResponse)
async def get_server_summary(server_id: str):
    sql = """
        SELECT
            s.server_id,
            s.name,
            s.verdict,
            s.risk_tier,
            s.last_assessed
        FROM mcp_server_registry s
        WHERE s.server_id = :server_id
    """
    result = query_write_service(sql, {"server_id": server_id})
    if not result:
        raise HTTPException(status_code=404, detail="Server not found")
    return result[0]

@app.get("/servers", response_model=ServerListResponse)
async def list_servers(
    risk_tier: Optional[str] = Query(None),
    limit: int = Query(50)
):
    sql = """
        SELECT
            server_id,
            name,
            risk_tier,
            verdict,
            last_assessed
        FROM mcp_server_registry
    """
    params = {}
    if risk_tier:
        sql += " WHERE risk_tier = :risk_tier"
        params["risk_tier"] = risk_tier
    sql += " LIMIT :limit"
    params["limit"] = limit
    result = query_write_service(sql, params)
    return {"servers": result}

@app.get("/axis-score-history/{server_id}", response_model=AxisScoreHistoryResponse)
async def get_axis_score_history(server_id: str):
    sql = """
        SELECT
            server_id,
            axis,
            score,
            timestamp
        FROM mcp_axis_score_history_api
        WHERE server_id = :server_id
        ORDER BY timestamp DESC
    """
    result = query_write_service(sql, {"server_id": server_id})
    if not result:
        raise HTTPException(status_code=404, detail="Server not found")
    return {"server_id": server_id, "history": result}

def run():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8791)

if __name__ == '__main__':
    # Mock write_service for testing
    def mock_query_write_service(sql: str, params: dict = None):
        if "mcp_server_registry" in sql:
            if params and params.get("server_id") == "test-server-id":
                return [{
                    "server_id": "test-server-id",
                    "name": "Test Server",
                    "verdict": "High Risk",
                    "risk_tier": "CAUTION_LIMITED",
                    "last_assessed": "2023-01-01"
                }]
            elif "WHERE risk_tier = :risk_tier" in sql and params.get("risk_tier") == "CAUTION_LIMITED":
                return [{
                    "server_id": "test-server-id",
                    "name": "Test Server",
                    "risk_tier": "CAUTION_LIMITED",
                    "verdict": "High Risk",
                    "last_assessed": "2023-01-01"
                }]
            else:
                return []
        elif "mcp_llm_axis_scores" in sql:
            return [{
                "server_id": "test-server-id",
                "axes": {
                    "axis1": {"score": 0.8, "p_top": "High"},
                    "axis2": {"score": 0.6, "p_top": "Medium"},
                    "axis3": {"score": 0.4, "p_top": "Low"},
                    "axis4": {"score": 0.7, "p_top": "Medium"},
                    "axis5": {"score": 0.5, "p_top": "Medium"},
                    "axis6": {"score": 0.3, "p_top": "Low"}
                }
            }]
        elif "mcp_axis_score_history_api" in sql:
            return [{
                "server_id": "test-server-id",
                "axis": "axis1",
                "score": 0.8,
                "timestamp": "2023-01-01"
            }]
        else:
            return []

    app.dependency_overrides[query_write_service] = mock_query_write_service

    client = TestClient(app)

    # Test health endpoint
    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service": "external_api_server"}

    # Test server verdict endpoint
    verdict_response = client.get("/servers/test-server-id/verdict")
    assert verdict_response.status_code == 200
    assert "risk_tier" in verdict_response.json()

    # Test server list endpoint
    servers_response = client.get("/servers?risk_tier=CAUTION_LIMITED")
    assert servers_response.status_code == 200
    assert "servers" in servers_response.json()

    print("PASS: external_api_server")