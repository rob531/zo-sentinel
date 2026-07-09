"""Hermetic tests for vuln_pkg_enricher + otx_threat_refs (no network: both
modules take injected fetch seams; sqlite in-memory DB)."""
import json
import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.db import Base                                   # noqa: E402
from app.models import (McpServerRegistry, ThreatIntelRef,  # noqa: E402
                        VulnAdvisory, VulnLink)
import otx_threat_refs                                    # noqa: E402
import vuln_pkg_enricher                                  # noqa: E402
from vuln_registry_linker import relink                   # noqa: E402


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _fake_manifests(mapping):
    return lambda url: mapping.get(url)


def test_enricher_stamps_npm_package(db):
    db.add(McpServerRegistry(server_id="s1", name="thing",
                             url="https://github.com/o/r"))
    db.commit()
    fetch = _fake_manifests({
        "https://raw.githubusercontent.com/o/r/HEAD/package.json":
            '{"name": "@scope/thing"}'})
    stats = vuln_pkg_enricher.enrich(db, fetch, limit=10)
    assert stats["stamped"] == 1
    meta = json.loads(db.get(McpServerRegistry, "s1").meta)
    assert meta["ecosystem"] == "npm" and meta["package"] == "@scope/thing"
    assert meta["pkg_identity_source"].endswith("package.json")  # provenance
    # idempotent: second pass skips
    assert vuln_pkg_enricher.enrich(db, fetch, limit=10)["already"] == 1


def test_enricher_never_guesses(db):
    db.add(McpServerRegistry(server_id="s2", url="https://github.com/o/none"))
    db.add(McpServerRegistry(server_id="s3", url="https://example.com/site"))
    db.commit()
    stats = vuln_pkg_enricher.enrich(db, _fake_manifests({}), limit=10)
    assert stats["stamped"] == 0
    assert stats["no_manifest"] == 1        # repo exists, manifest doesn't -> skip
    assert stats["no_repo_key"] == 1        # non-git URL -> skip
    assert db.get(McpServerRegistry, "s2").meta is None


def test_enrichment_unlocks_pkg_exact_link(db):
    """The point of the PR: advisory with NO repo ref links via pkg identity
    after enrichment."""
    db.add(McpServerRegistry(server_id="s1", url="https://github.com/o/r"))
    db.add(VulnAdvisory(id="GHSA-1", feed="osv", severity="HIGH",
                        ecosystem="npm", package="@scope/thing",
                        source_url="https://osv.dev/vulnerability/GHSA-1",
                        identities=["pkg:npm/@scope/thing"]))
    db.commit()
    assert relink(db)["links_created"] == 0          # repo-only server: no match
    fetch = _fake_manifests({
        "https://raw.githubusercontent.com/o/r/HEAD/package.json":
            '{"name": "@scope/thing"}'})
    vuln_pkg_enricher.enrich(db, fetch, limit=10)
    st = relink(db)
    assert st["links_created"] == 1
    link = db.execute(select(VulnLink)).scalars().one()
    assert link.match_basis == "package_exact"
    assert link.match_confidence == 1.0


def _otx_general(pulses):
    return {"pulse_info": {"count": len(pulses), "pulses": pulses}}


def test_otx_refresh_records_provenance_and_flags_aggregators(db):
    os.environ["ZO_VULN_OTX_ENABLED"] = "1"
    try:
        db.add(McpServerRegistry(server_id="s1", url="https://github.com/o/r"))
        db.add(McpServerRegistry(server_id="s2", url="https://mcp.example.io/x",
                                 risk_tier="HIGH"))
        db.add(VulnAdvisory(id="GHSA-1", feed="osv", ecosystem="npm",
                            package="p", aliases=["CVE-2026-1"],
                            source_url="https://osv.dev/vulnerability/GHSA-1"))
        db.add(VulnLink(advisory_id="GHSA-1", server_id="s1",
                        match_basis="repo_exact", match_value="repo:github.com/o/r",
                        match_confidence=1.0))
        db.commit()
        calls = []

        def fetch(itype, value):
            calls.append((itype, value))
            if itype == "cve":
                return _otx_general([
                    {"id": "p1", "name": "Actor exploits it", "indicator_count": 3},
                    {"id": "p2", "name": "Known_Cve | roundup", "indicator_count": 5000}])
            return _otx_general([])

        stats = otx_threat_refs.refresh(db, fetch)
        assert ("cve", "CVE-2026-1") in calls           # driven from OUR keys
        assert ("domain", "mcp.example.io") in calls
        assert all(v != "github.com" for t, v in calls if t == "domain")
        assert stats["refs_created"] == 2
        refs = {r.pulse_id: r for r in db.execute(select(ThreatIntelRef)).scalars()}
        assert refs["p1"].is_aggregator is False
        assert refs["p2"].is_aggregator is True
        assert refs["p1"].source_url.endswith("/pulse/p1")   # provenance anchor
        # idempotent
        assert otx_threat_refs.refresh(db, fetch)["refs_created"] == 0
    finally:
        os.environ.pop("ZO_VULN_OTX_ENABLED", None)


def test_otx_kill_switch_gates_everything(db):
    os.environ["ZO_VULN_OTX_ENABLED"] = "0"
    try:
        out = otx_threat_refs.refresh(db, lambda t, v: _otx_general([]))
        assert out == {"enabled": False, "note": out["note"]}   # refused, no claims
        assert otx_threat_refs.kill_switch_on() is False
    finally:
        os.environ.pop("ZO_VULN_OTX_ENABLED", None)
