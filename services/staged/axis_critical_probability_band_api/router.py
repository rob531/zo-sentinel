from __future__ import annotations

from datetime import datetime
import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import get_axis_critical_probability_band as _get_axis_critical_probability_band
except ImportError:
    try:
        from .logic import get_axis_critical_band as _get_axis_critical_probability_band
    except ImportError:
        try:
            from .logic import get_critical_probability_band as _get_axis_critical_probability_band
        except ImportError:
            from .logic import get_critical_probability_bands as _get_axis_critical_probability_band


router = APIRouter(prefix="/api")


class CriticalBand(BaseModel):
    band: str
    count: int
    server_ids: list[str]


class AxisCriticalProbabilityBandResponse(BaseModel):
    axis_name: str
    bands: list[CriticalBand]
    total: int
    generated_at: str


def _normalise_result(result: Any, axis_name: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {
            "axis_name": axis_name,
            "bands": result or [],
            "total": 0,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    generated_at = result.get("generated_at")
    if isinstance(generated_at, datetime):
        generated_at = generated_at.isoformat()
    elif not generated_at:
        generated_at = datetime.utcnow().isoformat() + "Z"

    bands = []
    for band in result.get("bands") or []:
        if isinstance(band, dict):
            bands.append(
                {
                    "band": str(band.get("band", "")),
                    "count": int(band.get("count", 0)),
                    "server_ids": [str(server_id) for server_id in band.get("server_ids", [])],
                }
            )

    return {
        "axis_name": str(result.get("axis_name", axis_name)),
        "bands": bands,
        "total": int(result.get("total", sum(band["count"] for band in bands))),
        "generated_at": str(generated_at),
    }


@router.get(
    "/axis/critical-band",
    response_model=AxisCriticalProbabilityBandResponse,
)
async def get_axis_critical_probability_band(
    axis_name: str = Query(default="overall_risk"),
    limit: int = Query(default=100, ge=1, le=1000),
    session=Depends(get_session),
) -> dict[str, Any]:
    result = _get_axis_critical_probability_band(
        axis_name=axis_name,
        limit=limit,
        session=session,
    )
    if inspect.isawaitable(result):
        result = await result
    return _normalise_result(result, axis_name)


async def _self_test() -> None:
    import httpx
    from fastapi import FastAPI

    rows = [
        {
            "server_id": "server-1",
            "axis_name": "overall_risk",
            "p_critical": 0.8,
            "p_danger": 0.1,
        },
        {
            "server_id": "server-2",
            "axis_name": "overall_risk",
            "p_critical": 0.5,
            "p_danger": 0.6,
        },
        {
            "server_id": "server-3",
            "axis_name": "overall_risk",
            "p_critical": 0.1,
            "p_danger": 0.1,
        },
        {
            "server_id": "server-4",
            "axis_name": "other_axis",
            "p_critical": 0.9,
            "p_danger": 0.1,
        },
    ]

    async def fake_logic(*, axis_name: str, limit: int, session):
        selected = [row for row in rows if row["axis_name"] == axis_name][:limit]
        buckets = {
            "critical_ge_0.7": [],
            "danger_0.4_0.7": [],
            "elevated_0.2_0.4": [],
            "nominal_lt_0.2": [],
        }
        for row in selected:
            probability = max(row["p_critical"], row["p_danger"])
            if probability >= 0.7:
                bucket = "critical_ge_0.7"
            elif probability >= 0.4:
                bucket = "danger_0.4_0.7"
            elif probability >= 0.2:
                bucket = "elevated_0.2_0.4"
            else:
                bucket = "nominal_lt_0.2"
            buckets[bucket].append(str(row["server_id"]))

        return {
            "axis_name": axis_name,
            "bands": [
                {
                    "band": name,
                    "count": len(server_ids),
                    "server_ids": server_ids,
                }
                for name, server_ids in buckets.items()
                if server_ids
            ],
            "total": len(selected),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    original_logic = globals()["_get_axis_critical_probability_band"]
    globals()["_get_axis_critical_probability_band"] = fake_logic

    that_app = FastAPI()
    that_app.include_router(router)

    async def override_session():
        yield object()

    that_app.dependency_overrides[get_session] = override_session

    try:
        transport = httpx.ASGITransport(app=that_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/axis/critical-band",
                params={"axis_name": "overall_risk", "limit": 100},
            )
            assert response.status_code == 200
            payload = response.json()
            assert set(payload) == {
                "axis_name",
                "bands",
                "total",
                "generated_at",
            }
            assert len(payload["bands"]) == 3
            critical = next(
                band for band in payload["bands"] if band["band"] == "critical_ge_0.7"
            )
            assert critical["count"] == 1
            assert payload["total"] == 3
    finally:
        globals()["_get_axis_critical_probability_band"] = original_logic

    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_self_test())