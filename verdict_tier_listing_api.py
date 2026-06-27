# verdict_tier_listing_api.py

from fastapi import FastAPI
from fastapi.testclient import TestClient

# PRODUCT_SPEC §2: Defined verdict tiers
VERDICT_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
]

app = FastAPI()

@app.get("/verdict_tiers", response_model=list[str])
async def get_verdict_tiers():
    """
    Returns a JSON list of all defined verdict tiers.
    """
    return VERDICT_TIERS

if __name__ == "__main__":
    client = TestClient(app)
    expected_tiers = [
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    ]

    response = client.get("/verdict_tiers")

    assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
    assert response.json() == expected_tiers, f"Expected {expected_tiers}, got {response.json()}"

    print("PASS")