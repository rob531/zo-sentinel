"""Regression test for zo-sentinel #12368: the public per-server read endpoints
must return 200 from the local DB (no :8772 write-service hop) and must tolerate
NULL score columns (p_critical/p_danger/label_index are routinely NULL).

Covers all five endpoints fixed together:
  GET /servers                            (server_pagination_api -- was 500: DI bug, db never passed)
  GET /servers/{id}/risk_axes             (server_risk_axes_api -- was 500: :8772)
  GET /servers/{id}/axis-scores-summary   (server_axis_scores_summary_router -- was 500: :8772 + injection)
  GET /axis-scores/servers/{id}           (axis_scores_query_api -- was 500: NULL vs non-Optional model)
  GET /axis-scores/summary/{id}           (axis_scores_query_api -- was 500: invalid-for-Postgres GROUP BY)
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore

import axis_scores_query_api
import server_pagination_api
import server_risk_axes_api
import server_axis_scores_summary_router

SID = "srv_test_1"


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(McpServerRegistry(server_id=SID, name="t", registry_source="npm",
                             verdict="CAUTION", risk_tier="MEDIUM"))
    # two rows for the SAME axis across model_versions (the table is UNIQUE on
    # server_id,axis_name,model_version) -> summary must return only the NEWEST (DISTINCT ON)
    db.add(McpLlmAxisScore(id=1, server_id=SID, axis_name="overall_risk", model_version="v3",
                           label="OLD", label_index=1, p_top=0.1, p_critical=0.1, p_danger=0.1,
                           escalated=False, scored_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    db.add(McpLlmAxisScore(id=2, server_id=SID, axis_name="overall_risk", model_version="v5",
                           label="NEW", label_index=2, p_top=0.9, p_critical=0.9, p_danger=0.9,
                           escalated=True, scored_at=datetime(2026, 6, 1, tzinfo=timezone.utc)))
    # a row with NULL p_critical/p_danger/label_index/scored_at -> must NOT 500
    db.add(McpLlmAxisScore(id=3, server_id=SID, axis_name="auth_strength", model_version="v5",
                           label="UNKNOWN", label_index=None, p_top=0.8, p_critical=None,
                           p_danger=None, escalated=None, scored_at=None))
    db.commit()

    app = FastAPI()
    for mod in (axis_scores_query_api, server_pagination_api,
                server_risk_axes_api, server_axis_scores_summary_router):
        app.include_router(mod.router)
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_servers_list_200(client):
    r = client.get("/servers?limit=5")
    assert r.status_code == 200, r.text
    assert any(s["server_id"] == SID for s in r.json()["servers"])


def test_risk_axes_200_and_null_tolerant(client):
    r = client.get(f"/servers/{SID}/risk_axes")
    assert r.status_code == 200, r.text
    axes = r.json()["axes"]
    assert axes["auth_strength"]["p_critical"] is None   # NULL tolerated, not 500


def test_axis_scores_summary_router_200(client):
    r = client.get(f"/servers/{SID}/axis-scores-summary")
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 2


def test_axis_scores_servers_200(client):
    r = client.get(f"/axis-scores/servers/{SID}")
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 3


def test_axis_scores_summary_latest_per_axis(client):
    r = client.get(f"/axis-scores/summary/{SID}")
    assert r.status_code == 200, r.text
    rows = {row["axis_name"]: row for row in r.json()["rows"]}
    # one row per axis; overall_risk resolves to the NEWEST (label "NEW")
    assert rows["overall_risk"]["label"] == "NEW"
