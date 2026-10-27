from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

try:
    from app.models import McpServerRegistry as RegistryServer
except ImportError:
    from app.models import McpServerRegistry as RegistryServer

from .logic import compute_source_mix

router = APIRouter()


class SourceMixItem(BaseModel):
    registry_source: str
    server_count: int
    tier_counts: Dict[str, int]
    avg_trust_score: float


class SourceMixResponse(BaseModel):
    sources: List[SourceMixItem]
    total_servers: int


@router.get(
    "/registry/source-mix",
    response_model=SourceMixResponse,
)
def get_registry_source_mix(
    session=Depends(get_session),
) -> SourceMixResponse:
    records = (
        session.query(
            RegistryServer.registry_source,
            RegistryServer.risk_tier,
            RegistryServer.trust_score,
        )
        .all()
    )

    rows = [
        {
            "registry_source": registry_source,
            "risk_tier": risk_tier,
            "trust_score": trust_score,
        }
        for registry_source, risk_tier, trust_score in records
    ]

    return SourceMixResponse(**compute_source_mix(rows))


if __name__ == "__main__":
    sample_rows = [
        {
            "registry_source": "source-b",
            "risk_tier": "high",
            "trust_score": 0.8,
        },
        {
            "registry_source": "source-b",
            "risk_tier": "low",
            "trust_score": 0.6,
        },
        {
            "registry_source": "source-a",
            "risk_tier": "medium",
            "trust_score": 0.7,
        },
    ]
    sample = compute_source_mix(sample_rows)

    assert sample["total_servers"] == 3
    assert len(sample["sources"]) == 2
    assert sample["sources"][0]["registry_source"] == "source-b"
    assert sample["sources"][0]["server_count"] == 2
    assert sum(sample["sources"][0]["tier_counts"].values()) == 2
    assert sample["sources"][1]["registry_source"] == "source-a"
    assert sum(sample["sources"][1]["tier_counts"].values()) == 1

    print("PASS")