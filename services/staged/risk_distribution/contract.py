import json
from typing import Any, Generator

import requests
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"

app = FastAPI()


@app.get("/api/risk/distribution")
def get_risk_distribution() -> dict[str, list[dict[str, Any]]]:
    sql = "SELECT risk_tier, COUNT(*) FROM McpServerRegistry GROUP BY risk_tier"
    response = requests.post(WRITE_SERVICE_URL, json={"query": sql})
    result = response.json()
    
    distribution = [
        {"tier": row["risk_tier"], "count": row["count"]}
        for row in result.get("rows", [])
    ]
    
    return {"distribution": distribution}


if __name__ == "__main__":
    import requests_mock
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    
    session.add(McpServerRegistry(
        name="server-a",
        risk_tier="TRUSTED_GENERAL",
        confidence=0.95,
        description="General purpose server",
    ))
    session.add(McpServerRegistry(
        name="server-b",
        risk_tier="HIGH_RISK_ISOLATED",
        confidence=0.3,
        description="High risk isolated server",
    ))
    session.add(McpServerRegistry(
        name="server-c",
        risk_tier="TRUSTED_GENERAL",
        confidence=0.92,
        description="Another general server",
    ))
    session.commit()
    
    def build_distribution_response(request, context):
        query = json.loads(request.text).get("query", "")
        if "GROUP BY risk_tier" in query:
            return {
                "rows": [
                    {"risk_tier": "TRUSTED_GENERAL", "count": 2},
                    {"risk_tier": "HIGH_RISK_ISOLATED", "count": 1},
                ]
            }
        return {"rows": []}
    
    with requests_mock.Mocker() as mocker:
        mocker.post(WRITE_SERVICE_URL, json=build_distribution_response)
        
        app.dependency_overrides[get_session] = lambda: session
        from fastapi.testclient import TestClient
        client = TestClient(app)
        response = client.get("/api/risk/distribution")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert len(data["distribution"]) == 2, f"Expected 2 tiers, got {len(data['distribution'])}"
        trusted_count = next(
            (d["count"] for d in data["distribution"] if d["tier"] == "TRUSTED_GENERAL"),
            None
        )
        assert trusted_count == 2, f"Expected TRUSTED_GENERAL count 2, got {trusted_count}"
        
        print("PASS")