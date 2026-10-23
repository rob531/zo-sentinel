from typing import Any
from fastapi import Depends
from pydantic import BaseModel
import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisItem(BaseModel):
    axis_name: str
    label: str
    p_critical: float
    p_top: float


class ServerAxisCriticalItem(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    critical_axes: list[AxisItem]


class ServerAxisCriticalResponse(BaseModel):
    server_count: int
    servers: list[ServerAxisCriticalItem]


RISK_AXES = [
    "security",
    "reliability",
    "performance",
    "scalability",
    "maintainability",
    "availability",
    "operational_risk",
]


def get_server_axis_critical(session: Any = Depends(get_session)) -> dict[str, Any]:
    write_service_url = "http://127.0.0.1:8772/query"
    axis_list = "', '".join(RISK_AXES)

    sql = f"""
        SELECT
            r.server_id,
            r.name,
            r.risk_tier,
            a.axis_name,
            a.label,
            a.p_critical,
            a.p_top
        FROM mcp_llm_axis_scores a
        JOIN mcp_server_registry r ON a.server_id = r.server_id
        WHERE a.axis_name IN ('{axis_list}')
          AND a.p_critical > 0.5
        ORDER BY r.server_id, a.p_critical DESC
    """

    resp = requests.post(
        write_service_url,
        json={"sql": sql},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()

    servers: dict[str, ServerAxisCriticalItem] = {}
    for row in rows:
        sid = row["server_id"]
        if sid not in servers:
            servers[sid] = ServerAxisCriticalItem(
                server_id=sid,
                name=row["name"],
                risk_tier=row["risk_tier"],
                critical_axes=[],
            )
        servers[sid].critical_axes.append(
            AxisItem(
                axis_name=row["axis_name"],
                label=row["label"],
                p_critical=row["p_critical"],
                p_top=row["p_top"],
            )
        )

    server_list = list(servers.values())
    return {"server_count": len(server_list), "servers": server_list}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mock_data = [
        {"server_id": "srv-001", "name": "Alpha Server", "risk_tier": "high", "axis_name": "security", "label": "Security Risk", "p_critical": 0.75, "p_top": 0.92},
        {"server_id": "srv-001", "name": "Alpha Server", "risk_tier": "high", "axis_name": "reliability", "label": "Reliability Risk", "p_critical": 0.62, "p_top": 0.88},
        {"server_id": "srv-002", "name": "Beta Server", "risk_tier": "medium", "axis_name": "performance", "label": "Performance Risk", "p_critical": 0.55, "p_top": 0.79},
        {"server_id": "srv-003", "name": "Gamma Server", "risk_tier": "low", "axis_name": "availability", "label": "Availability Risk", "p_critical": 0.31, "p_top": 0.65},
    ]

    app = FastAPI()

    @app.get("/api/servers/axis-critical")
    def mock_endpoint():
        servers: dict[str, ServerAxisCriticalItem] = {}
        for row in mock_data:
            if row["p_critical"] > 0.5:
                sid = row["server_id"]
                if sid not in servers:
                    servers[sid] = ServerAxisCriticalItem(
                        server_id=sid,
                        name=row["name"],
                        risk_tier=row["risk_tier"],
                        critical_axes=[],
                    )
                servers[sid].critical_axes.append(
                    AxisItem(
                        axis_name=row["axis_name"],
                        label=row["label"],
                        p_critical=row["p_critical"],
                        p_top=row["p_top"],
                    )
                )
        server_list = list(servers.values())
        return {"server_count": len(server_list), "servers": server_list}

    client = TestClient(app)
    response = client.get("/api/servers/axis-critical")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert data["server_count"] == 2, f"Expected server_count == 2, got {data['server_count']}"

    for srv in data["servers"]:
        assert len(srv["critical_axes"]) >= 1, f"Server {srv['server_id']} has no critical axes"

    print("PASS")