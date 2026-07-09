"""CI gate for the P2 vuln/OTX/CVE surfacing slice
(docs/DESIGN_NEXT_BUILD_TARGETS_2026_07.md; agent-built 2026-07-06).

Covers: module self-tests as subprocesses (sqlite, no network), the facet
enumerator actually gaining the two boolean facets through the best-effort
hook, per-server threat_refs split curated/aggregator, the coverage-SLA
metric, and THE LINE (kill-switch off => zero trace: no facets, no refs,
no coverage numbers).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO = pathlib.Path(__file__).resolve().parents[1]

MODULES = ["vuln_facet_extension.py", "vuln_coverage_sla_api.py"]


@pytest.mark.parametrize("module", MODULES)
def test_selftest_passes(module):
    env = {**os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": ""}
    proc = subprocess.run([sys.executable, str(REPO / module)],
                          capture_output=True, text=True, timeout=120,
                          env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0 and "PASS" in out, f"{module}\n{out[-2000:]}"


def _seeded_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import (McpServerRegistry, ThreatIntelRef, VulnAdvisory,
                            VulnLink)
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        McpServerRegistry(server_id="s1", url="https://github.com/o/r",
                          risk_tier="HIGH"),
        McpServerRegistry(server_id="s2", url="https://mcp.example.io/x"),
        McpServerRegistry(server_id="s3", url="https://github.com/o/clean"),
        VulnAdvisory(id="GHSA-1", feed="ghsa", severity="HIGH", summary="rce",
                     source_url="https://github.com/advisories/GHSA-1",
                     aliases=["CVE-2025-1111"],
                     fetched_at=datetime(2026, 7, 4, 12, 0)),
        VulnLink(advisory_id="GHSA-1", server_id="s1", match_basis="repo_exact",
                 match_value="repo:github.com/o/r", match_confidence=1.0),
        ThreatIntelRef(indicator_type="cve", indicator_value="CVE-2025-1111",
                       pulse_id="p1", pulse_name="curated report",
                       is_aggregator=False, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p1"),
        ThreatIntelRef(indicator_type="cve", indicator_value="CVE-2025-1111",
                       pulse_id="p2", pulse_name="daily roundup",
                       is_aggregator=True, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p2"),
        ThreatIntelRef(indicator_type="domain", indicator_value="mcp.example.io",
                       pulse_id="p3", pulse_name="malicious hosting",
                       is_aggregator=False, source="otx",
                       source_url="https://otx.alienvault.com/pulse/p3"),
    ])
    s.commit()
    return s


def test_facet_enumerator_gains_vuln_facets(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "pol.json"))
    monkeypatch.setenv("ZO_VULN_ENABLED", "1")
    from facet_enum_service import compute_facets
    s = _seeded_session()
    f = compute_facets(s)
    assert f["has_known_cve"][0] == {"value": "true", "count": 1}
    assert f["referenced_in_threat_intel"][0] == {"value": "true", "count": 2}
    # boolean facets partition the registry
    assert sum(d["count"] for d in f["has_known_cve"]) == 3
    assert sum(d["count"] for d in f["referenced_in_threat_intel"]) == 3


def test_per_server_refs_and_coverage(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "pol.json"))
    monkeypatch.setenv("ZO_VULN_ENABLED", "1")
    from vuln_facet_extension import server_threat_refs
    from vuln_coverage_sla_api import compute_coverage
    s = _seeded_session()
    r = server_threat_refs(s, "s1")
    assert r["status"] == "ok" and r["count"] == 2
    assert [x["is_aggregator"] for x in r["refs"]] == [False, True]  # curated first
    assert all(x["source_url"].startswith("https://") for x in r["refs"])  # provenance
    assert server_threat_refs(s, "s2")["count"] == 1   # via hosting domain
    assert server_threat_refs(s, "s3")["count"] == 0
    c = compute_coverage(s)
    assert (c["registry_total"], c["linked_servers"], c["coverage_pct"]) == (3, 1, 33.33)
    assert c["newest_advisory_fetched_at"] == "2026-07-04T12:00:00"


def test_kill_switch_off_leaves_zero_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "pol.json"))
    monkeypatch.setenv("ZO_VULN_ENABLED", "0")
    from facet_enum_service import compute_facets
    from vuln_facet_extension import server_threat_refs
    s = _seeded_session()
    f = compute_facets(s)
    assert "has_known_cve" not in f and "referenced_in_threat_intel" not in f
    assert server_threat_refs(s, "s1") == {"status": "disabled",
                                           "server_id": "s1", "refs": []}


def test_view_renders_insufficient_and_provenance():
    html = (REPO / "server_threat_intel_view.html").read_text(encoding="utf-8")
    assert "INSUFFICIENT" in html                  # honest-degrade wording
    assert "curated" in html and "aggregator" in html
    assert "/threat_refs" in html and "/vulns" in html
    assert "__CLERK_PK__" in html                  # auth injection slot
