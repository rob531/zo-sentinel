"""The mount tool must refuse what a static label would have waved through.

WHY THIS TEST EXISTS
--------------------
`tools/orphanage.py` labels an orphan MOUNTABLE -- "clean + data-wired:
promotion candidate (keep, mount)" -- from a STATIC TEXT SCAN. It never imports
the module. Measured 2026-09-28 on origin/main `bf0a5a25f`, of the 28 deferred
modules carrying that label:

    import succeeds and exposes `router` ....... 12
    ImportError / ModuleNotFoundError .......... 16
    imports, but its route is already served ... 1

So the label is a CANDIDATE SET, never a decision, and the triage arithmetic on
#3996/#4004/#4005 was computed on it. `tools/mount_deferred_router.py` exists to
put the runtime pole back in (HARNESS_DOCTRINE R1), and these tests keep the
refusal poles OBSERVED rather than asserted -- each one drives `_decide` to a
different REFUSED verdict, so none of them is an untested branch (R4).
"""

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_PATH = os.path.join(ROOT, "tools", "mount_deferred_router.py")


def _load_tool():
    spec = importlib.util.spec_from_file_location("_mount_deferred_router", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    # registered BEFORE exec so the module can be imported by its own name
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


CENSUS = {"m": {"module": "m", "why_unmounted": "MOUNTABLE",
                "declared_prefix": "/x", "imports_data_layer": True}}


def _decide(tool, monkeypatch, probe, deferred=None, served=None, name="m",
            root_file=True):
    monkeypatch.setattr(tool, "_probe", lambda module: probe)
    monkeypatch.setattr(os.path, "isfile",
                        lambda p: root_file if p.endswith(name + ".py") else False)
    return tool._decide(name, {name: "deferred for test"} if deferred is None else deferred,
                        CENSUS, {} if served is None else served)


def test_refuses_a_module_whose_import_fails(tool, monkeypatch):
    """The 16/28 case. A MOUNTABLE label plus a failing import is a refusal."""
    verdict, detail, _ = _decide(
        tool, monkeypatch,
        {"import_ok": False, "error": "ImportError: cannot import name 'ServiceHealth'"})
    assert verdict == "REFUSED_IMPORT"
    assert "ServiceHealth" in detail


def test_refuses_a_module_with_no_router(tool, monkeypatch):
    verdict, _, _ = _decide(tool, monkeypatch,
                            {"import_ok": True, "has_router": False, "paths": []})
    assert verdict == "REFUSED_NO_ROUTER"


def test_refuses_a_route_already_served(tool, monkeypatch):
    """The 1/28 case: SUPERSEDED that the static classifier missed."""
    verdict, detail, _ = _decide(
        tool, monkeypatch,
        {"import_ok": True, "has_router": True, "paths": ["/a", "/b"]},
        served={"/b": "perspective_diff_service"})
    assert verdict == "REFUSED_ROUTE_COLLISION"
    assert "/b" in detail and "perspective_diff_service" in detail


def test_refuses_a_router_that_was_never_deferred(tool, monkeypatch):
    """This tool drains the declared deferral list; it is not a back door for
    mounting an undeclared router."""
    verdict, _, _ = _decide(tool, monkeypatch,
                            {"import_ok": True, "has_router": True, "paths": ["/a"]},
                            deferred={})
    assert verdict == "REFUSED_NOT_DEFERRED"


def test_accepts_only_when_the_import_was_observed(tool, monkeypatch):
    """The positive pole -- without it the refusals above prove only that the
    tool refuses everything."""
    verdict, detail, _ = _decide(tool, monkeypatch,
                                 {"import_ok": True, "has_router": True,
                                  "paths": ["/fresh"]})
    assert verdict == "MOUNT"
    assert "/fresh" in detail


def test_no_module_is_both_mounted_and_deferred():
    """Parity latch for the half-applied state this tool heals: a module
    registered in services/active/ must not still be named in the deferral
    list, in either direction of drift."""
    deferred = json.load(open(os.path.join(ROOT, "tools", "reachability_deferred.json"),
                              encoding="utf-8"))["deferred"]
    active = os.path.join(ROOT, "services", "active")
    registered = {d for d in os.listdir(active)
                  if os.path.isfile(os.path.join(active, d, "service.toml"))}
    both = sorted(registered & set(deferred))
    assert both == [], (
        "registered in services/active/ AND still deferred: %r -- run "
        "tools/mount_deferred_router.py --all-mountable --apply to heal" % both)
