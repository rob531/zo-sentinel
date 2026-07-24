"""CI gate for the vuln-intel spine + Scan-my-config killer feature.

Runs each module's real __main__ self-test as a subprocess (sqlite, no
network — the OSV fetch is an injected seam) and requires PASS. Plus a
cross-module integration test proving the whole chain on real rows:
ingest -> link -> exposure (with kill-switch) -> scan-my-config, and that THE
LINE holds (no vuln claim without provenance; kill-switch off => no claims).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO = pathlib.Path(__file__).resolve().parents[1]

MODULES = [
    "vuln_identity.py",
    "vuln_osv_ingestor.py",
    "vuln_registry_linker.py",
    "vuln_exposure_api.py",
    "config_scan_api.py",
]


@pytest.mark.parametrize("module", MODULES)
def test_selftest_passes(module):
    env = {**os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": ""}
    env.pop("ASK_LLM", None)
    proc = subprocess.run([sys.executable, str(REPO / module)],
                          capture_output=True, text=True, timeout=120,
                          env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0 and "PASS" in out, f"{module}\n{out[-2000:]}"


def test_full_chain_ingest_link_expose_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("ZO_POLICY_OVERRIDE_PATH", str(tmp_path / "pol.json"))
    monkeypatch.setenv("ZO_VULN_ENABLED", "1")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpLlmAxisScore, McpServerRegistry
    from vuln_osv_ingestor import ingest
    from vuln_registry_linker import relink
    from vuln_exposure_api import server_vulns
    from config_scan_api import scan_config

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(McpServerRegistry(server_id="s1", name="mcp-inspector",
                            url="https://github.com/anthropics/mcp-inspector",
                            risk_tier="HIGH", verdict="HIGH"))
    s.add(McpLlmAxisScore(id=1, server_id="s1", axis_name="auth_strength",
                          label="WEAK", model_version="v3"))
    s.commit()

    osv = [{"id": "GHSA-abc-1", "summary": "auth bypass",
            "database_specific": {"severity": "CRITICAL",
                                  "url": "https://github.com/advisories/GHSA-abc-1"},
            "affected": [{"package": {"ecosystem": "npm", "name": "@mcp/inspector"}}],
            "references": [{"url": "https://github.com/anthropics/mcp-inspector"}]}]
    assert ingest(s, lambda: osv)["written"] == 1
    assert relink(s)["links_created"] == 1

    # exposure carries provenance
    ex = server_vulns(s, "s1")
    assert ex["status"] == "ok" and ex["count"] == 1
    v = ex["vulns"][0]
    assert v["source_url"].startswith("https://") and v["match_confidence"] == 1.0

    # scan-my-config surfaces the linked vuln + the axes + honest unknown
    cfg = json.dumps({"mcpServers": {
        "inspector": {"args": ["github.com/anthropics/mcp-inspector"]},
        "mystery": {"command": "node"}}})
    out = scan_config(s, cfg)
    assert out["summary"]["identified"] == 1 and out["summary"]["unknown"] == 1
    assert out["summary"]["with_vulns"] == 1
    scored = [r for r in out["results"] if r["status"] == "scored"][0]
    assert scored["vulns"][0]["id"] == "GHSA-abc-1"

    # THE LINE: kill-switch off => scan reports no vuln claims (disabled)
    monkeypatch.setenv("ZO_VULN_ENABLED", "0")
    out2 = scan_config(s, cfg)
    scored2 = [r for r in out2["results"] if r["status"] == "scored"][0]
    assert scored2["vulns_status"] == "disabled" and not scored2["vulns"]


def test_no_fuzzy_matching(tmp_path, monkeypatch):
    """A near-miss repo must NOT link -- exact identity only."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry
    from vuln_osv_ingestor import ingest
    from vuln_registry_linker import relink
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(McpServerRegistry(server_id="s1", name="x",
                            url="https://github.com/anthropics/mcp-inspector-fork"))
    s.commit()
    osv = [{"id": "G-1", "database_specific": {"severity": "HIGH"},
            "affected": [{"package": {"ecosystem": "npm", "name": "@mcp/inspector"}}],
            "references": [{"url": "https://github.com/anthropics/mcp-inspector"}]}]
    ingest(s, lambda: osv)
    assert relink(s)["links_created"] == 0   # -fork is a different repo


def test_scan_and_views_mounted():
    """SOA update (2026-07-24): mount truth = services/active/ registry + the
    generated spine (app/_spine_generated.py), not a hand-list in main.py."""
    spine = (REPO / "app" / "_spine_generated.py").read_text(encoding="utf-8")
    for mod in ("vuln_exposure_api", "config_scan_api", "vuln_osv_ingestor",
                "vuln_registry_linker"):
        assert (REPO / "services" / "active" / mod / "service.toml").exists(), \
            f"{mod} not registered in services/active/"
        assert f'"{mod}"' in spine, f"{mod} registered but absent from the generated spine"
    main = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert '"/scan"' in main
    html = (REPO / "scan_view.html").read_text(encoding="utf-8")
    assert "localStorage" not in html and "/api/scan" in html


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
