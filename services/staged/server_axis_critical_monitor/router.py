from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
import requests

from app.db import get_session


RISK_AXES = [
    'sabotage',
    'social_engineering',
    'data_theft',
    'service_theft',
    'adversarial_injection',
    'supply_chain',
    'credential_theft',
]


class AxisItem(BaseModel):
    axis_name: str
    label: str
    p_critical: float
    p_top: float


class ServerAxisCriticalItem(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    critical_axes: List[AxisItem]


class ServerAxisCriticalResponse(BaseModel):
    server_count: int
    servers: List[ServerAxisCriticalItem]


def get_critical_servers(write_service_url: str = "http://127.0.0.1:8772") -> ServerAxisCriticalResponse:
    axis_list = ", ".join([f"'{ax}'" for ax in RISK_AXES])
    sql = f"""
        SELECT
            sr.server_id,
            sr.name,
            sr.risk_tier,
            ax.axis_name,
            ax.label,
            ax.p_critical,
            ax.p_top
        FROM mcp_llm_axis_scores ax
        JOIN mcp_server_registry sr ON ax.server_id = sr.server_id
        WHERE ax.axis_name IN ({axis_list})
          AND ax.p_critical > 0.5
        ORDER BY sr.server_id, ax.p_critical DESC
    """
    payload = {"sql": sql, "params": {}}
    resp = requests.post(f"{write_service_url}/query", json=payload, timeout=30)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])

    servers_map = {}
    for row in rows:
        sid = row["server_id"]
        if sid not in servers_map:
            servers_map[sid] = {
                "server_id": sid,
                "name": row["name"],
                "risk_tier": row["risk_tier"],
                "critical_axes": [],
            }
        servers_map[sid]["critical_axes"].append(
            AxisItem(
                axis_name=row["axis_name"],
                label=row["label"],
                p_critical=row["p_critical"],
                p_top=row["p_top"],
            )
        )

    items = [ServerAxisCriticalItem(**v) for v in servers_map.values()]
    return ServerAxisCriticalResponse(
        server_count=len(items),
        servers=items,
    )


router = APIRouter()


@router.get("/servers/axis-critical", response_model=ServerAxisCriticalResponse)
def list_axis_critical_servers() -> ServerAxisCriticalResponse:
    return get_critical_servers()


if __name__ == "__main__":
    import unittest.mock

    mock_data = {
        "rows": [
            {
                "server_id": "server1",
                "name": "Server One",
                "risk_tier": "critical",
                "axis_name": "sabotage",
                "label": "Sabotage Risk",
                "p_critical": 0.6,
                "p_top": 0.8,
            },
            {
                "server_id": "server2",
                "name": "Server Two",
                "risk_tier": "high",
                "axis_name": "social_engineering",
                "label": "Social Engineering Risk",
                "p_critical": 0.7,
                "p_top": 0.9,
            },
        ]
    }

    mock_response = unittest.mock.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_data

    with unittest.mock.patch("requests.post", return_value=mock_response):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_session] = lambda: None

        client = TestClient(app)
        response = client.get("/api/servers/axis-critical")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["server_count"] == 2, f"Expected server_count 2, got {data['server_count']}"
        assert len(data["servers"]) == 2
        for srv in data["servers"]:
            assert len(srv["critical_axes"]) >= 1

        print("PASS")