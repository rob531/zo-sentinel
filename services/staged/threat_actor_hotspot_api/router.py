from datetime import datetime
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef

from .logic import get_hotspots as _get_hotspots


router = APIRouter(prefix="/api")


class SampleServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None


class ThreatActorHotspot(BaseModel):
    actor_id: Union[int, str]
    actor_label: Optional[str] = None
    pulse_name: Optional[str] = None
    indicator_type: Optional[str] = None
    server_count: int = Field(ge=0)
    worst_risk_tier: Optional[str] = None
    sample_servers: List[SampleServer] = Field(default_factory=list)


class ThreatActorHotspotResponse(BaseModel):
    days: int
    generated_at: datetime
    total_actors: int = Field(ge=0)
    hotspots: List[ThreatActorHotspot] = Field(default_factory=list)


@router.get(
    "/threat/actors/hotspots",
    response_model=ThreatActorHotspotResponse,
    tags=["threat-intelligence"],
)
def get_threat_actor_hotspots(
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=20, ge=1),
    min_servers: int = Query(default=1, ge=1),
    session=Depends(get_session),
) -> ThreatActorHotspotResponse:
    return _get_hotspots(session, days, limit, min_servers)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield object()

    test_app.dependency_overrides[get_session] = override_get_session

    original_get_hotspots = _get_hotspots

    def fake_get_hotspots(session, days, limit, min_servers):
        return {
            "days": days,
            "generated_at": datetime.now().isoformat(),
            "total_actors": 2,
            "hotspots": [
                {
                    "actor_id": "actor-1",
                    "actor_label": "Actor One",
                    "pulse_name": "Pulse One",
                    "indicator_type": "ip",
                    "server_count": 2,
                    "worst_risk_tier": "high",
                    "sample_servers": [
                        {
                            "server_id": "server-1",
                            "name": "Server One",
                            "risk_tier": "high",
                        },
                        {
                            "server_id": "server-2",
                            "name": "Server Two",
                            "risk_tier": "medium",
                        },
                    ],
                },
                {
                    "actor_id": "actor-2",
                    "actor_label": "Actor Two",
                    "pulse_name": "Pulse Two",
                    "indicator_type": "domain",
                    "server_count": 1,
                    "worst_risk_tier": "medium",
                    "sample_servers": [
                        {
                            "server_id": "server-3",
                            "name": "Server Three",
                            "risk_tier": "medium",
                        }
                    ],
                },
            ],
        }

    globals()["_get_hotspots"] = fake_get_hotspots
    try:
        response = TestClient(test_app).get("/api/threat/actors/hotspots")
        assert response.status_code == 200
        payload = response.json()
        assert payload["hotspots"][0]["server_count"] == 2
        assert len(payload["hotspots"]) == 2
        assert all(
            hotspot["pulse_name"] and hotspot["actor_label"]
            for hotspot in payload["hotspots"]
        )
    finally:
        globals()["_get_hotspots"] = original_get_hotspots

    print("PASS")