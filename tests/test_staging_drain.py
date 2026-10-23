"""staging_drain -- the chain that leaves no staged service without a reason.

Unit tests on synthetic census records (no live services, no subprocesses) for the
deterministic parts: family/version split, failure classification, the outcome
ledger (S3 dedupe + S6 retire + S5 routing), the exclusion file and its readers,
and the daily line. Plus the one integration-shaped check that matters: the
proposal fan-out REJECTS a build_service for an excluded family (the S7 gate), and
the exclusion reader distinguishes "nothing excluded" from "file unreadable".
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.staging_drain import census, classify, exclusions, report  # noqa: E402
from zo_sentinel import builder_exclusions as bx  # noqa: E402


def _rec(name, *, source=True, router=True, verdict="HOLD", first_failure="", rc=None,
         files=None, source_import_ok=None, stage=None):
    fam, ver = census.split_family(name)
    files = files if files is not None else (["__init__.py", "router.py", "service.toml"] if source else ["service.toml"])
    return {
        "service": name, "family": fam, "version": ver, "files": files,
        "has_source": source, "has_toml": "service.toml" in files, "toml_valid": "service.toml" in files,
        "has_router": router, "runtime_class_observed": rc or ("api" if router else ("lib" if source else "none")),
        "scaffold_stage": stage or ("complete" if router else ("logic_only" if source else "manifest_only")),
        "gate": {"verdict": verdict, "reasons": [first_failure] if first_failure else []},
        "first_failure": first_failure,
        "failure_class": census.classify_failure(first_failure, verdict),
        "source_import_ok": source_import_ok, "source_import": None, "footprint_delta_kb": None,
    }


def _census(*recs):
    return {"generated_at": "t", "basis": {"mode": "full-gate", "git_head": "abc"}, "services": list(recs)}


# ---------------------------------------------------------------- S1 helpers

def test_family_version_split():
    assert census.split_family("cve_linker_v3") == ("cve_linker", 3)
    assert census.split_family("cve_linker") == ("cve_linker", 1)
    assert census.split_family("v2_thing") == ("v2_thing", 1)


@pytest.mark.parametrize("line,expected", [
    ("", "none"),
    ("service.toml missing/invalid (need name + import_path)", "missing_or_invalid_toml"),
    ("router.py exposes no router", "missing_router"),
    ("route collision with active: {'/api/ask': 'ask_answer_api'}", "route_collision"),
    ("a service named 'x' is already active", "name_already_active"),
    ("hollow member(s): services/staged/x/contract.py -- ...", "hollow_member"),
    ("IMPORT FAILED (would ModuleNotFoundError at spine mount): ImportError: cannot import name 'MeshMemory' from 'app.models'", "import_model_name"),
    ("IMPORT FAILED (would ModuleNotFoundError at spine mount): ModuleNotFoundError: No module named 'services.staged.x.logic'", "import_module_not_found"),
    ("IMPORT FAILED (would ModuleNotFoundError at spine mount): NameError: name 'services' is not defined", "import_undefined_name"),
    ("contract FAILED: exit=1 /usr/bin/python3: No module named services.staged.x.contract", "contract_missing"),
    ("contract FAILED: exit=1 AssertionError: 500", "contract_failed"),
    ("contract FAILED: contract TIMEOUT (120s)", "contract_timeout"),
    ("test-only import at module scope (breaks the prod spine at import, release v86): router.py:10 fastapi.testclient.TestClient", "test_only_import_at_module_scope"),
    ("mount probe FAILED (real router on the exemplar harness): GET /api/x -> 500 over an empty data layer", "mount_probe_failed"),
])



def test_failure_classes(line, expected):
    assert census.classify_failure(line, "PROMOTE" if not line else "HOLD") == expected


def test_module_scope_test_imports_and_contract_shape():
    src = ("from fastapi import APIRouter\nfrom fastapi.testclient import TestClient\n"
           "def f():\n    from unittest.mock import patch\n"
           "if __name__ == '__main__':\n    import pytest\n")
    found = census.module_scope_test_imports(src)
    assert found == [(2, "fastapi.testclient.TestClient")], found   # guarded/inner imports are fine
    assert census.CONTRACT_ROUTER_IMPORT_RE.search("from .router import router\n")
    assert census.CONTRACT_ROUTER_IMPORT_RE.search("from services.staged.x.router import router\n")
    assert not census.CONTRACT_ROUTER_IMPORT_RE.search("router = MagicMock()\n")
    assert census.CONTRACT_MOCK_RE.search("from unittest.mock import MagicMock, patch\n")
    assert not census.CONTRACT_MOCK_RE.search("# no mock data here\n")


# ---------------------------------------------------------------- S3 / S6 ledger

def test_every_service_gets_exactly_one_outcome():
    c = _census(
        _rec("alpha", verdict="PROMOTE"),
        _rec("beta_v2", first_failure="contract FAILED: exit=1 boom"),
        _rec("beta", verdict="PROMOTE"),
        _rec("gamma", source=False),
        _rec("delta", router=False, rc="lib", source_import_ok=True, files=["logic.py", "service.toml"]),
    )
    led = classify.classify(c, promotions={}, active={})
    assert set(led["services"]) == {"alpha", "beta_v2", "beta", "gamma", "delta"}
    assert led["services"]["alpha"]["outcome"] == "promotable"      # gate green != promoted
    assert led["services"]["gamma"]["outcome"] == "retired"
    assert "no source" in led["services"]["gamma"]["reason"]
    # family beta: v2 fails, v1 passes -> keep the newest PASSING version; v2 superseded
    assert led["services"]["beta"]["outcome"] == "promotable"
    assert led["services"]["beta_v2"]["outcome"] == "superseded"
    assert led["services"]["beta_v2"]["superseded_by"] == "beta"
    # a lib that imports but has no router and no entrypoint is an unfinished api
    # scaffold: a repair target (missing router), never "promotable"
    assert led["services"]["delta"]["outcome"] == "repair"
    assert led["services"]["delta"]["failure_class"] == "lib_no_router"


def test_worker_with_entrypoint_that_imports_is_promotable():
    c = _census(_rec("osv_ingestor", router=False, rc="worker", source_import_ok=True,
                     files=["__init__.py", "logic.py", "service.toml"]))
    led = classify.classify(c, promotions={}, active={})
    assert led["services"]["osv_ingestor"]["outcome"] == "promotable"
    assert "zo scheduled job" in led["services"]["osv_ingestor"]["reason"]


def test_no_passing_version_keeps_newest_as_repair_target():
    c = _census(
        _rec("osv_v3", first_failure="contract FAILED: exit=1 boom"),
        _rec("osv_v2", first_failure="router.py exposes no router", router=False, rc="lib"),
        _rec("osv", first_failure="contract FAILED: exit=1 boom"),
    )
    led = classify.classify(c, promotions={}, active={})
    assert led["services"]["osv_v3"]["outcome"] == "repair"
    assert led["services"]["osv_v3"]["repair_path"] == "builder directive"
    assert led["services"]["osv_v2"]["superseded_by"] == "osv_v3"
    assert led["services"]["osv"]["superseded_by"] == "osv_v3"


def test_mechanical_classes_are_routed_to_scripts():
    c = _census(_rec("x", first_failure="service.toml missing/invalid (need name + import_path)",
                     files=["router.py"]))
    led = classify.classify(c, promotions={}, active={})
    row = led["services"]["x"]
    assert row["outcome"] == "repair"
    assert row["repair_path"].startswith("mechanical: python tools/check_service_manifests.py")


def test_active_family_supersedes_staged_copy():
    c = _census(_rec("widget", verdict="PROMOTE"), _rec("widget_v3", verdict="PROMOTE"))
    led = classify.classify(c, promotions={}, active={"widget": [(2, "widget_v2")]})
    assert led["services"]["widget"]["outcome"] == "superseded"
    assert led["services"]["widget"]["superseded_by"] == "active:widget_v2"
    assert led["services"]["widget_v3"]["outcome"] == "promotable"   # newer than active


def test_route_collision_is_superseded_by_the_route_owner():
    c = _census(_rec("ask_answer", first_failure="route collision with active: {'/api/ask': 'ask_answer_api'}"))
    led = classify.classify(c, promotions={}, active={})
    assert led["services"]["ask_answer"]["superseded_by"] == "active:ask_answer_api"


def test_promoted_only_with_evidence():
    c = _census(_rec("alpha", verdict="PROMOTE"))
    led = classify.classify(c, promotions={"alpha": {"summary": "v98", "evidence": {"deploy": "v98", "probe": "200"}}}, active={})
    assert led["services"]["alpha"]["outcome"] == "promoted"
    led2 = classify.classify(c, promotions={"alpha": {"summary": "claimed, no evidence"}}, active={})
    assert led2["services"]["alpha"]["outcome"] == "promotable"


def test_classify_refuses_a_static_only_census(tmp_path):
    cpath = tmp_path / "c.json"
    cpath.write_text(json.dumps({"generated_at": "t", "basis": {"mode": "static-only"}, "services": []}))
    assert classify.main(["--census", str(cpath), "--ledger", str(tmp_path / "l.json"),
                          "--promotions", str(tmp_path / "none.json")]) == 2


# ---------------------------------------------------------------- S7 exclusions

def test_exclusion_file_and_readers(tmp_path, monkeypatch):
    c = _census(_rec("widget", verdict="PROMOTE"), _rec("osv_v2", first_failure="contract FAILED: x"),
                _rec("osv", first_failure="contract FAILED: x"), _rec("ghost", source=False))
    led = classify.classify(c, promotions={"widget": {"summary": "v98", "evidence": {"deploy": "v98"}}},
                            active={"legacy": [(1, "legacy")]})
    active_dir = tmp_path / "active"
    (active_dir / "legacy").mkdir(parents=True)
    data = exclusions.build(led, active_dir=str(active_dir))
    fams = data["families"]
    assert "widget" in fams and fams["widget"]["reason"].startswith("promoted")
    assert "legacy" in fams and fams["legacy"]["reason"].startswith("active")
    # osv superseded by its OWN newer version is still being drained -> not excluded
    assert "osv" not in fams
    # a retired family is not excluded either: the builder may still complete it
    assert "ghost" not in fams
    path = tmp_path / "builder_exclusions.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("ZO_BUILDER_EXCLUSIONS", str(path))
    assert bx.load()["status"] == "ok"
    assert bx.excluded_reason("widget").startswith("promoted")
    assert bx.excluded_reason("widget_v4").startswith("promoted")
    assert bx.excluded_reason("build_service_widget").startswith("promoted")
    assert bx.excluded_reason("widget.py").startswith("promoted")
    assert bx.excluded_reason("osv_v2") is None
    assert {"widget", "widget.py", "legacy", "legacy.py"} <= bx.excluded_names()


def test_unreadable_exclusions_exclude_nothing_but_say_so(tmp_path, monkeypatch):
    monkeypatch.setenv("ZO_BUILDER_EXCLUSIONS", str(tmp_path / "missing.json"))
    assert bx.load()["status"] == "missing"
    assert bx.excluded_reason("anything") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("ZO_BUILDER_EXCLUSIONS", str(bad))
    assert bx.load()["status"] == "unreadable"
    assert bx.excluded_names() == set()


def test_fanout_rejects_excluded_family(tmp_path, monkeypatch):
    from zo_sentinel.promoters import proposed_to_pending_promoter as promoter
    path = tmp_path / "ex.json"
    path.write_text(json.dumps({"families": {"widget": {"reason": "active: live"}}}))
    monkeypatch.setenv("ZO_BUILDER_EXCLUSIONS", str(path))
    proposed = tmp_path / "proposed"
    proposed.mkdir()
    spec = "GET /api/widget/list returns the widget list over the real data layer; " * 2
    (proposed / "a.json").write_text(json.dumps({"handler": "build_service", "service_name": "widget_v9", "spec": spec}))
    (proposed / "b.json").write_text(json.dumps({"handler": "build_service", "service_name": "gadget", "spec": spec}))
    n = promoter._expand_service_directives(proposed)
    names = sorted(p.name for p in proposed.iterdir())
    assert "a.json.excluded" in names, names
    assert "a.json" not in names
    assert n == 1  # gadget fanned out, widget_v9 did not
    assert not any(n_.startswith("svc_services_staged_widget") for n_ in names)


# ---------------------------------------------------------------- report

def test_daily_line_counts_promotable_as_remaining_and_undirected_repairs_as_remaining():
    led = {"services": {
        "a": {"outcome": "promoted"}, "b": {"outcome": "superseded"}, "c": {"outcome": "retired"},
        "d": {"outcome": "repair", "directive": "directives/pending/x.json"},
        "e": {"outcome": "repair", "directive": None, "repair_path": "builder directive"},
        "f": {"outcome": "promotable", "runtime_class": "api"},
    }}
    c = report.counts(led)
    assert (c["promoted"], c["superseded"], c["repairing"], c["retired"], c["remaining"]) == (1, 1, 1, 1, 2)
    line = report.daily_line(c, {"status": "unmeasured", "detail": "x"})
    assert line.startswith("staging_drain: promoted 1 · superseded 1 · repairing 1 · retired 1 · remaining 2 · wall: image size UNKNOWN")
    line2 = report.daily_line(c, {"status": "measured", "final_rss_kb": 300 * 1024, "services": 3, "budget_mb": 512})
    assert "wall: none" in line2
    line3 = report.daily_line(c, {"status": "measured", "final_rss_kb": 900 * 1024, "services": 3, "budget_mb": 512})
    assert "wall: image size (" in line3
    line4 = report.daily_line(c, {"status": "measured", "final_rss_kb": 900 * 1024, "services": 3, "budget_mb": None})
    assert "wall: image size UNKNOWN budget" in line4
