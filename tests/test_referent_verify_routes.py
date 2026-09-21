"""The ROUTES half of referent verification -- the half that is ARMED.

referent-verify runs with `--enforce-checks routes`: a routes FAIL or UNKNOWN
fails the job, while tables/columns stay report-only on a pre-2026-08-11
backlog. These tests guard the two properties that arming depends on.

The hole arming would otherwise open: a service that declares no router is
SKIPPED rather than failed -- correct, and the only reason routes can be green
at all -- but an unbounded skip list means a NEW service could quietly join it
and the armed gate would stay green. So every skip must be DECLARED in
tools/spine_known_issues.json, and an undeclared one is a routes FAILURE.
"""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _rv():
    spec = importlib.util.spec_from_file_location(
        "referent_verify", ROOT / "tools" / "referent_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _declared_no_router():
    ki = json.loads((ROOT / "tools" / "spine_known_issues.json").read_text())
    return {e["service"] for e in ki.get("known", []) if e.get("status") == "NO_ROUTER"}


def test_every_no_router_skip_is_declared():
    """The armed gate's load-bearing assumption."""
    res = _rv().check_routes()
    undeclared = sorted(s for s in res["skipped_no_router"]
                        if s not in _declared_no_router())
    assert undeclared == [], (
        "these services mount NO router and are not declared in "
        "tools/spine_known_issues.json, so they would be skipped silently by an "
        f"armed routes gate: {undeclared}. Declare them with a reason, or give "
        "them a router.")


def test_undeclared_skip_is_reported_as_a_routes_failure():
    """An undeclared skip must FAIL, not warn -- otherwise arming buys nothing."""
    rv = _rv()
    res = rv.check_routes()
    assert "undeclared_no_router" in res, \
        "check_routes must report the undeclared set for the armed gate to use it"
    if res["skipped_no_router"]:
        # Simulate one skip losing its declaration and confirm the verdict flips.
        victim = res["skipped_no_router"][0]
        real = rv.check_routes
        ki_path = ROOT / "tools" / "spine_known_issues.json"
        original = ki_path.read_text()
        try:
            d = json.loads(original)
            d["known"] = [e for e in d.get("known", [])
                          if e.get("service") != victim]
            ki_path.write_text(json.dumps(d, indent=2))
            again = real()
            assert again["verdict"] == "FAIL", (
                f"removing {victim} from the declared list left the routes "
                f"verdict at {again['verdict']}; the skip list is not enforced")
            assert victim in again["undeclared_no_router"]
        finally:
            ki_path.write_text(original)


def test_routes_verdict_is_currently_pass():
    """Routes is the half that is solved; arming asserts it stays solved."""
    res = _rv().check_routes()
    assert res["verdict"] == "PASS", f"{res['verdict']}: {res['detail']}"
    assert res["failures"] == [], res["failures"]
    assert res.get("unresolved") == [], res.get("unresolved")
