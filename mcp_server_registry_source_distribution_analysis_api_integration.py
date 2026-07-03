# deps: fastapi pytest sqlalchemy
from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Base
from mcp_server_registry_source_distribution_analysis_api import router


def test_source_distribution_api():
    # Create an in-memory SQLite database
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed test data
    def seed_data():
        db = SessionLocal()
        try:
            # Add test servers
            db.add(McpServerRegistry(server_id="srv1", name="Test Server 1",
                                    url="https://test1.com", registry_source="source1"))
            db.add(McpServerRegistry(server_id="srv2", name="Test Server 2",
                                    url="https://test2.com", registry_source="source2"))

            # Add test scores
            for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"),
                                        ("auth_strength", "STRONG"),
                                        ("capability_breadth", "BROAD"),
                                        ("data_sensitivity", "CRITICAL"),
                                        ("network_egress", "EXTERNAL"),
                                        ("maintainer_trust", "ESTABLISHED"),
                                        ("exploit_surface", "MODERATE")), start=1):
                db.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                                          model_version="v3.0_40974559"))
            db.commit()
        finally:
            db.close()

    # Override the get_session dependency
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create the FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed the test data
    seed_data()

    # Create a test client
    client = TestClient(app)

    # Test 1: GET /source-distribution with valid parameters
    response = client.get("/api/source-distribution?source=source1")
    assert response.status_code == 200
    data = response.json()
    assert "source_distribution" in data
    assert len(data["source_distribution"]) > 0

    # Test 2: GET /source-distribution with invalid parameters
    response = client.get("/api/source-distribution?source=invalid_source")
    assert response.status_code == 200
    data = response.json()
    assert "source_distribution" in data
    assert len(data["source_distribution"]) == 0

    # Test 3: GET /source-distribution with missing parameters
    response = client.get("/api/source-distribution")
    assert response.status_code == 400

    print("PASS")

if __name__ == "__main__":
    test_source_distribution_api()