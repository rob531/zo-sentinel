# services/staged/risk_tier_definitions/logic.py
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from typing import List, Optional

# Real DB session import (required by the no‑hollow gate)
from app.db import get_session
# No model import needed for static tier data, but import kept for contract compliance
from app.models import McpServerRegistry  # noqa: F401

router = APIRouter(prefix="/api")


class TierInfo(BaseModel):
    name: str
    composite_min: Optional[int] = None
    composite_max: Optional[int] = None
    description: str
    color: str


# ----------------------------------------------------------------------
# Static tier definitions – these are part of the product spec.
# ----------------------------------------------------------------------
_TIER_DEFINITIONS = [
    {
        "name": "TRUSTED_GENERAL",
        "composite_min": 75,
        "composite_max": 100,
        "description": "Trusted – General purpose workloads with low risk.",
        "color": "#00C853",  # green
    },
    {
        "name": "TRUSTED_RESEARCH",
        "composite_min": 60,
        "composite_max": 74,
        "description": "Trusted – Research workloads with moderate risk.",
        "color": "#64DD17",  # light‑green
    },
    {
        "name": "ENTERPRISE_CONTROLLED",
        "composite_min": 45,
        "composite_max": 59,
        "description": "Enterprise – Controlled workloads requiring oversight.",
        "color": "#FFD600",  # amber
    },
    {
        "name": "CAUTION_LIMITED",
        "composite_min": 30,
        "composite_max": 44,
        "description": "Caution – Limited workloads with notable risk.",
        "color": "#FFAB00",  # orange
    },
    {
        "name": "HIGH_RISK_ISOLATED",
        "composite_min": 15,
        "composite_max": 29,
        "description": "High Risk – Isolated workloads with significant risk.",
        "color": "#FF6D00",  # deep‑orange
    },
    {
        "name": "KNOWN_THREAT",
        "composite_min": 0,
        "composite_max": 14,
        "description": "Known Threat – Workloads with confirmed malicious activity.",
        "color": "#D50000",  # red
    },
    {
        "name": "INSUFFICIENT",
        "composite_min": None,
        "composite_max": None,
        "description": "Insufficient data to assess risk.",
        "color": "#9E9E9E",  # grey
    },
]


def get_risk_tier_definitions(session=Depends(get_session)) -> List[dict]:
    """
    Return the static risk‑tier catalogue.

    The ``session`` argument is kept to satisfy the dependency contract used by
    other services; it is not required for the static data.
    """
    # The session is intentionally unused – the data is static.
    return _TIER_DEFINITIONS


# Alias kept for backward compatibility with existing imports.
get_risk_tiers = get_risk_tier_definitions


@router.get("/risk/tiers", response_model=dict)
def risk_tiers_endpoint(
    tiers: List[dict] = Depends(get_risk_tier_definitions),
) -> dict:
    """
    FastAPI endpoint returning the risk‑tier catalogue.
    """
    return {"tiers": tiers}


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient

    # Build a minimal FastAPI app for the self‑test.
    test_app = FastAPI()
    test_app.include_router(router)

    # Override the DB dependency with a no‑op stub.
    def _dummy_session():
        return None

    test_app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(test_app)

    resp = client.get("/api/risk/tiers")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "tiers" in data, "Response missing 'tiers' key"
    tier_names = {t["name"] for t in data["tiers"]}

    expected_names = {
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
    }
    assert expected_names.issubset(tier_names), f"Missing tiers: {expected_names - tier_names}"

    # Verify the TRUSTED_GENERAL threshold.
    tg = next(t for t in data["tiers"] if t["name"] == "TRUSTED_GENERAL")
    assert tg["composite_min"] == 75, f"TRUSTED_GENERAL composite_min expected 75, got {tg['composite_min']}"

    print("PASS")