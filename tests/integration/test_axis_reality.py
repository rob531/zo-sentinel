"""Gate-2 axis-reality E2E: prove the verdict API returns REAL values for ALL 7
named axes from the real schema for a known server, and FAILS CLOSED on a
missing/null/synthetic axis. Runs against Postgres (the real backend) via the
app's own engine/session (DATABASE_URL).

This is the gate the council (CONTRA/FATHER) required before the prod flip:
"green CI" previously did not prove the live read path returns real axis data.

Runs only on Postgres (the nightly `axis-reality` job / the prod backend). The
real schema's BigInteger identity PK needs a sequence, which sqlite lacks, so the
per-PR sqlite `pytest` tier skips this rather than producing a false red.
"""
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

pytestmark = pytest.mark.skipif(
    "postgres" not in os.environ.get("DATABASE_URL", ""),
    reason="axis-reality gate runs on Postgres (nightly axis-reality job / prod backend)",
)

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
    from app import db as appdb
    from app.db import Base
    import app.models as models
    import verdict_breakdown_api as vapi
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    Base.metadata.create_all(appdb.engine)

    s = appdb.SessionLocal()
    s.query(models.McpLlmAxisScore).filter_by(server_id=SERVER).delete()
    s.query(models.McpServerRegistry).filter_by(server_id=SERVER).delete()
    s.commit()
    s.add(models.McpServerRegistry(server_id=SERVER, name="known-mcp",
                                   url="https://known.example",
                                   risk_tier="HIGH_RISK_ISOLATED"))
    for ax in AXES:
        s.add(models.McpLlmAxisScore(server_id=SERVER, axis_name=ax, label=SEEDED[ax],
                                     label_index=1, p_top=0.7, model_version=MV))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(vapi.router)
    admin = vapi.Principal(user_id="admin-test", role="admin")
    app.dependency_overrides[vapi.get_principal] = lambda: admin
    if hasattr(vapi, "require_admin"):
        app.dependency_overrides[vapi.require_admin] = lambda: admin
    return TestClient(app)


def test_verdict_returns_all_7_real_axes(client):
    r = client.get(f"/api/verdict/{SERVER}")
    assert r.status_code == 200, r.text
    body = r.json()
    axes = body["axes"]
    assert set(axes.keys()) == set(AXES), f"axis set drift: {sorted(axes)}"
    for ax in AXES:
        lbl = axes[ax]["label"]
        assert lbl not in (None, "", "STUB", "SYNTHETIC", "UNKNOWN_STUB"), f"{ax} null/synthetic: {lbl!r}"
        assert lbl == SEEDED[ax], f"{ax} not read from DB: got {lbl!r} want {SEEDED[ax]!r}"
    assert body["model_version"] == MV


def test_absent_server_404_not_fabricated(client):
    assert client.get("/api/verdict/__no_such_server__").status_code == 404
