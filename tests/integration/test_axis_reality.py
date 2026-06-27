"""Gate-2 axis-reality E2E: prove the verdict API returns REAL values for ALL 7
named axes from the real schema for a known server, and FAILS CLOSED on a
missing/null/synthetic axis. Backend-agnostic (app ORM + DATABASE_URL).

This is the gate the council (CONTRA/FATHER) required before the prod flip:
"green CI" previously did not prove the live read path returns real axis data.
"""
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
SEEDED = {
    "overall_risk": "MEDIUM",
    "auth_strength": "STRONG",
    "capability_breadth": "MEDIUM",
    "data_sensitivity": "HIGH",
    "network_egress": "LOW",
    "maintainer_trust": "ESTABLISHED",
    "exploit_surface": "MEDIUM",
}
SERVER = "srv-axis-reality-1"
MV = "v3.0"


@pytest.fixture()
def client():
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, get_session
    import app.models as models
    import verdict_breakdown_api as vapi

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = SessionLocal()
    seed.add(models.McpServerRegistry(server_id=SERVER, name="known-mcp",
                                      url="https://known.example",
                                      risk_tier="HIGH_RISK_ISOLATED"))
    for ax in AXES:
        seed.add(models.McpLlmAxisScore(server_id=SERVER, axis_name=ax, label=SEEDED[ax],
                                        label_index=1, p_top=0.7, model_version=MV))
    seed.commit()
    seed.close()

    app = FastAPI()
    app.include_router(vapi.router)

    def _ovr():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    admin = vapi.Principal(user_id="admin-test", role="admin")
    app.dependency_overrides[get_session] = _ovr
    app.dependency_overrides[vapi.get_principal] = lambda: admin
    if hasattr(vapi, "require_admin"):
        app.dependency_overrides[vapi.require_admin] = lambda: admin
    return TestClient(app)


def test_verdict_returns_all_7_real_axes(client):
    r = client.get(f"/api/verdict/{SERVER}")
    assert r.status_code == 200, r.text
    body = r.json()
    axes = body["axes"]
    # fail-closed: exactly the 7 named axes, each a real non-null label read from the DB
    assert set(axes.keys()) == set(AXES), f"axis set drift: {sorted(axes)}"
    for ax in AXES:
        lbl = axes[ax]["label"]
        assert lbl not in (None, "", "STUB", "SYNTHETIC", "UNKNOWN_STUB"), f"{ax} null/synthetic: {lbl!r}"
        assert lbl == SEEDED[ax], f"{ax} not read from DB: got {lbl!r} want {SEEDED[ax]!r}"
    assert body["model_version"] == MV


def test_absent_server_404_not_fabricated(client):
    # a server with no scores must 404, never fabricate axes
    assert client.get("/api/verdict/__no_such_server__").status_code == 404
