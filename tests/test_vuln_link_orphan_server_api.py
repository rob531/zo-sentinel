"""services/staged/vuln_link_orphan_server_api: the router imported a .logic that was
never written (#16248/#16251), so promotion would have died at import. Now it has one."""
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink
from services.staged.vuln_link_orphan_server_api.router import router


def _client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)
    s = TS()
    s.add(McpServerRegistry(server_id="live", name="L", url="https://l"))
    for aid, sev in (("CVE-1", "HIGH"), ("CVE-2", "LOW")):
        s.add(VulnAdvisory(id=aid, feed="osv", severity=sev, source_url="https://osv.dev/" + aid))
    s.add_all([
        VulnLink(advisory_id="CVE-1", server_id="live", match_basis="repo_exact", match_value="x", match_confidence=1.0),
        VulnLink(advisory_id="CVE-1", server_id="gone", match_basis="repo_exact", match_value="x",
                 match_confidence=1.0, linked_at=datetime(2026, 11, 1)),
        VulnLink(advisory_id="CVE-2", server_id="gone", match_basis="package_exact", match_value="y",
                 match_confidence=0.5, linked_at=datetime(2026, 11, 2)),
        VulnLink(advisory_id="CVE-2", server_id="weak", match_basis="package_exact", match_value="z",
                 match_confidence=0.2),
    ])
    s.commit(); s.close()
    app = FastAPI(); app.include_router(router)

    def _o():
        d = TS()
        try:
            yield d
        finally:
            d.close()
    app.dependency_overrides[get_session] = _o
    return TestClient(app)


def test_orphans_exclude_registered_servers_and_group_per_server():
    r = _client().get("/api/vuln/links/orphans")
    assert r.status_code == 200, r.text
    j = r.json()
    ids = [o["server_id"] for o in j["orphans"]]
    assert "live" not in ids and ids[0] == "gone" and j["count"] == 2
    gone = j["orphans"][0]
    assert gone["link_count"] == 2 and gone["advisory_count"] == 2
    assert gone["severities"] == ["HIGH", "LOW"] and gone["max_match_confidence"] == 1.0


def test_min_confidence_filters_links():
    j = _client().get("/api/vuln/links/orphans?min_confidence=0.6").json()
    assert [o["server_id"] for o in j["orphans"]] == ["gone"] and j["orphans"][0]["link_count"] == 1
