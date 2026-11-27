"""GR-2 (MERGE_AUDIT_2026-08-23 G1): app.main is owned by a tier and fails loud.

The defect: tier4_spine swallowed an `import app.main` failure as a PASSED check
("skipped: app.main import owned by tier1") while tier1's allowlist held no `app`
module, so neither tier tested the ASGI entrypoint. The kill (#3986) is merged;
this pins it by defect injection, so re-introducing the swallow -- or dropping
app.main from tier1 -- is a red required pytest check.
"""
import json
import sys

import pytest

from tests.ci import hermetic_manifest as M
from tests.ci import smoke_ladder as L


def _check(tier, name):
    hits = [c for c in tier.checks if c.name == name]
    assert hits, f"{name} not recorded; checks={[c.name for c in tier.checks]}"
    return hits[0]


def test_tier1_owns_app_main():
    assert "app.main" in M.IMPORTABLE_MODULES


def test_unimportable_app_main_is_a_failed_check_not_a_warning(monkeypatch):
    # Defect injection: make `import app.main` raise, exactly as a broken app/db.py would.
    monkeypatch.setitem(sys.modules, "app.main", None)
    t = L.tier4_spine()
    c = _check(t, "spine::app_main_imports")
    assert c.passed is False, "tier4 swallowed an unimportable app.main (the G1 shape)"
    assert "UNIMPORTABLE" in c.detail


def test_stale_known_runtime_entry_fails(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "spine_known_issues.json").write_text(json.dumps(
        {"known": [], "known_runtime": [{"service": "no_such_service_gr2", "error_contains": ""}]}))
    monkeypatch.setattr(L, "REPO_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(M.REPO_ROOT))
    t = L.tier4_spine()
    if not _check(t, "spine::app_main_imports").passed:
        pytest.skip("app.main does not import in this environment; covered by the test above")
    c = _check(t, "spine::known_runtime_not_stale")
    assert c.passed is False and "no_such_service_gr2" in c.detail
