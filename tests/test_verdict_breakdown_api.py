"""pytest for verdict_breakdown_api -- real router over SQLite (get_session overridden),
proving it reads McpLlmAxisScore/McpServerRegistry and applies trust_gating_override."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry
import verdict_breakdown_api as vapi

MV = "v3.0_40974559"
_AXES = (("overall_risk", "HIGH"), ("auth_strength", "STRONG"), ("capability_breadth", "BROAD"),
         ("data_sensitivity", "CRITICAL"), ("network_egress", "EXTERNAL"),
         ("maintainer_trust", "ESTABLISHED"), ("exploit_surface", "MODERATE"))


@pytest.fixture()
def client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    # official publisher (Stripe) -> should be capped by the trust override
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    # unknown publisher -> not capped
    s.add(McpServerRegistry(server_id="srv2", name="rando", url="https://github.com/nobody/x"))
    _id = 0
    for sid in ("srv1", "srv2"):
        for ax, lbl in _AXES:
            _id += 1
            # srv2 is an unknown publisher: make its maintainer_trust UNKNOWN so neither the
            # verified-org allow-list nor the ESTABLISHED signal grants it the cap.
            if sid == "srv2" and ax == "maintainer_trust":
                lbl = "UNKNOWN_AUTHOR"
            s.add(McpLlmAxisScore(id=_id, server_id=sid, axis_name=ax, label=lbl, model_version=MV))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(vapi.router)

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_official_publisher_capped(client):
    r = client.get("/api/verdict/srv1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["axes"]) == 7
    assert j["model_overall_risk"] == "HIGH"
    assert j["published_overall_risk"] == "MEDIUM"   # trust_gating_override caps Stripe
    assert j["trusted"] is True


def test_unknown_publisher_not_capped(client):
    j = client.get("/api/verdict/srv2").json()
    assert j["model_overall_risk"] == "HIGH"
    assert j["published_overall_risk"] == "HIGH"      # not trusted -> passthrough
    assert j["trusted"] is False


def test_missing_server_404(client):
    assert client.get("/api/verdict/does-not-exist").status_code == 404
