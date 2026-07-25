from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import McpScoreTier
from fastapi.testclient import TestClient
import requests

router = APIRouter()

class ThresholdResponse(BaseModel):
    tiers: Dict[str, float]
    source: str

DEFAULT_THRESHOLDS = {
    "TRUSTED_GENERAL": 0.9,
    "TRUSTED_LIMITED": 0.75,
    "RESTRICTED": 0.5,
    "BLOCKED": 0.25
}

def get_thresholds(include_defaults: Optional[bool] = False) -> ThresholdResponse:
    session = Depends(get_session)
    custom_tiers = session.query(McpScoreTier).all()
    tiers = {tier.name: tier.cutoff for tier in custom_tiers}

    if not tiers and include_defaults:
        tiers = DEFAULT_THRESHOLDS
        source = "default"
    elif tiers:
        source = "custom"
    else:
        raise HTTPException(status_code=404, detail="No thresholds found")

    return ThresholdResponse(tiers=tiers, source=source)

router.get("/risk_tier/thresholds")(get_thresholds)

def write_service_query(query: str, params: dict = None):
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session as original_get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[original_get_session] = override_get_session

    with TestSession() as session:
        session.add(McpScoreTier(name="TRUSTED_GENERAL", cutoff=0.85))
        session.add(McpScoreTier(name="TRUSTED_LIMITED", cutoff=0.7))
        session.commit()

    client = TestClient(app)
    response = client.get("/risk_tier/thresholds?include_defaults=true")
    assert response.status_code == 200
    data = response.json()
    assert data["tiers"]["TRUSTED_GENERAL"] == 0.85
    assert data["source"] == "custom"
    print("PASS")