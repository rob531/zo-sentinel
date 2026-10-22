from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compare_servers


router = APIRouter(prefix="/api", tags=["server-verdict-compare"])


class CompareRequest(BaseModel):
    server_ids: list[str] = Field(default_factory=list)


class AxisScore(BaseModel):
    label: str
    p_top: float


class AxisDiff(BaseModel):
    delta: float
    leader: str
    tied: bool


class ServerComparison(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    axes: dict[str, AxisScore]


class CompareResponse(BaseModel):
    servers: list[ServerComparison]
    axis_order: list[str]
    diff: dict[str, AxisDiff]
    compared: int


@router.post("/compare", response_model=CompareResponse)
def compare(
    request: CompareRequest,
    session: Session = Depends(get_session),
) -> CompareResponse:
    server_ids = [server_id.strip() for server_id in request.server_ids if server_id.strip()]
    if not server_ids:
        raise HTTPException(status_code=400, detail="server_ids must contain at least one server")
    if len(server_ids) > 5:
        raise HTTPException(status_code=400, detail="server_ids may contain at most five servers")

    try:
        result = compare_servers(server_ids, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CompareResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: object()

    original_compare_servers = compare_servers

    def compare_servers_for_test(
        server_ids: list[str], session: Session | None = None
    ) -> dict:
        axis_order = [
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        ]
        return {
            "servers": [
                {
                    "server_id": server_ids[0],
                    "name": "Server A",
                    "risk_tier": "high",
                    "axes": {
                        axis_name: {"label": "high", "p_top": 0.9}
                        for axis_name in axis_order
                    },
                }
            ],
            "axis_order": axis_order,
            "diff": {
                axis_name: {"delta": 0.7, "leader": server_ids[0], "tied": False}
                for axis_name in axis_order
            },
            "compared": 1,
        }

    globals()["compare_servers"] = compare_servers_for_test
    try:
        with TestClient(test_app) as client:
            empty_response = client.post("/api/compare", json={"server_ids": []})
            assert empty_response.status_code == 400

            response = client.post("/api/compare", json={"server_ids": ["server-a"]})
            assert response.status_code == 200
            assert response.json()["compared"] == 1
            assert len(response.json()["axis_order"]) == 7
            assert len(response.json()["servers"][0]["axes"]) == 7
    finally:
        globals()["compare_servers"] = original_compare_servers

    print("PASS")