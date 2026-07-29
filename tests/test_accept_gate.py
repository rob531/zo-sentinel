"""Tests for tools/accept_gate.py -- the post-deploy acceptance verdict.

Every assertion here was seen RED before it was trusted: the suite was run
against three deliberate mutations of evaluate() (drop the git_sha check,
collapse ERROR into REJECT, treat a populated failures[] as ACCEPT) and each
mutation broke at least one test. An assertion never seen RED is not evidence.

The live negative control is recorded in the PR: prod at v64 (git_sha "unknown",
7 ModuleNotFoundError) returns REJECT, exit 1, with both reasons named.
"""

import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_GATE = os.path.join(os.path.dirname(_HERE), "tools", "accept_gate.py")

_spec = importlib.util.spec_from_file_location("accept_gate", _GATE)
accept_gate = importlib.util.module_from_spec(_spec)
sys.modules["accept_gate"] = accept_gate
_spec.loader.exec_module(accept_gate)

ACCEPT = accept_gate.ACCEPT
REJECT = accept_gate.REJECT
ERROR = accept_gate.ERROR

SHA = "7fc39201d8aea5f50017bf893843694e5a77f7f1"
OTHER_SHA = "0ada3c0c7cfabc5ca17f985528c17697cb5d8013"


def _health(status=200):
    return (status, {"status": "ok", "app": "MCPRisky", "env": "prod"})


def _version(sha=SHA, status=200):
    return (status, {"app": "MCPRisky", "env": "prod", "git_sha": sha, "db_reachable": True})


def _spine(ok=True, failures=None, status=200, service_count=31):
    return (
        status,
        {"ok": ok, "service_count": service_count, "failures": failures or []},
    )


def _fail(name):
    return {"service": name, "import_path": name, "error": "ModuleNotFoundError(%r)" % name}


# ------------------------------------------------------------------ ACCEPT


def test_all_green_accepts():
    verdict, reasons = accept_gate.evaluate(_health(), _version(), _spine(), SHA)
    assert verdict == ACCEPT
    assert SHA in reasons[0]


def test_accept_requires_all_three_surfaces_not_just_health():
    """A 200 on /health alone is the weakest possible signal -- the app answered."""
    verdict, _ = accept_gate.evaluate(_health(), _version(), _spine(ok=False), SHA)
    assert verdict == REJECT


# ------------------------------------------------------------------ git_sha


def test_unknown_git_sha_rejects():
    """The v64 state: prod cannot identify itself, so the gate cannot pass it."""
    verdict, reasons = accept_gate.evaluate(_health(), _version("unknown"), _spine(), SHA)
    assert verdict == REJECT
    assert any("UNANSWERABLE" in r for r in reasons)
    assert any("--build-arg GIT_SHA" in r for r in reasons)


def test_missing_git_sha_field_rejects():
    verdict, reasons = accept_gate.evaluate(_health(), (200, {"app": "MCPRisky"}), _spine(), SHA)
    assert verdict == REJECT
    assert any("git_sha" in r for r in reasons)


def test_wrong_git_sha_rejects_and_names_both():
    verdict, reasons = accept_gate.evaluate(_health(), _version(OTHER_SHA), _spine(), SHA)
    assert verdict == REJECT
    joined = " ".join(reasons)
    assert OTHER_SHA in joined and SHA in joined
    assert "DIFFERENT tree" in joined


# ------------------------------------------------------------------ spine


def test_populated_failures_rejects_and_names_the_services():
    spine = _spine(ok=False, failures=[_fail("org_api_key_manager"), _fail("threat_intel_summary_api")])
    verdict, reasons = accept_gate.evaluate(_health(), _version(), spine, SHA)
    assert verdict == REJECT
    joined = " ".join(reasons)
    assert "org_api_key_manager" in joined
    assert "threat_intel_summary_api" in joined


def test_ok_true_with_populated_failures_still_rejects():
    """Defence in depth: never trust one summary flag over the list itself."""
    verdict, reasons = accept_gate.evaluate(
        _health(), _version(), _spine(ok=True, failures=[_fail("x")]), SHA
    )
    assert verdict == REJECT
    assert any("failures[] is not empty" in r for r in reasons)


def test_ok_truthy_string_is_not_true():
    verdict, _ = accept_gate.evaluate(_health(), _version(), _spine(ok="true"), SHA)
    assert verdict == REJECT


def test_string_failures_are_named_without_crashing():
    verdict, reasons = accept_gate.evaluate(
        _health(), _version(), _spine(ok=False, failures=["plain_string_service"]), SHA
    )
    assert verdict == REJECT
    assert "plain_string_service" in " ".join(reasons)


# ------------------------------------------------------------------ ERROR


def test_unreachable_surface_is_error_not_reject():
    """'We could not tell' and 'it is broken' demand different actions."""
    verdict, reasons = accept_gate.evaluate((None, None), _version(), _spine(), SHA)
    assert verdict == ERROR
    assert any("unreachable" in r for r in reasons)


def test_non_json_body_is_error_not_reject():
    verdict, reasons = accept_gate.evaluate(_health(), (200, None), _spine(), SHA)
    assert verdict == ERROR
    assert any("not JSON" in r for r in reasons)


def test_error_wins_over_reject():
    """An unread surface must not be reported as a red just because another failed."""
    verdict, _ = accept_gate.evaluate((None, None), _version("unknown"), _spine(ok=False), SHA)
    assert verdict == ERROR


def test_error_is_never_zero():
    assert ERROR != ACCEPT
    assert REJECT != ACCEPT
    assert ACCEPT == 0


# ------------------------------------------------------------------ 5xx


def test_five_hundred_on_spine_rejects():
    verdict, reasons = accept_gate.evaluate(_health(), _version(), _spine(status=503), SHA)
    assert verdict == REJECT
    assert any("503" in r for r in reasons)


def test_five_hundred_on_health_rejects():
    verdict, reasons = accept_gate.evaluate(_health(500), _version(), _spine(), SHA)
    assert verdict == REJECT
    assert any("500" in r for r in reasons)


# ------------------------------------------------------------------ CLI contract


def test_short_sha_is_error_not_accept():
    assert accept_gate.main(["--sha", "7fc3920"]) == ERROR


def test_non_hex_sha_is_error():
    assert accept_gate.main(["--sha", "z" * 40]) == ERROR


def test_main_returns_the_verdict_as_exit_code(monkeypatch):
    monkeypatch.setattr(
        accept_gate, "poll", lambda *a, **k: (REJECT, ["synthetic"], {"health_status": 200})
    )
    assert accept_gate.main(["--sha", SHA, "--once"]) == REJECT


def test_main_error_verdict_propagates(monkeypatch):
    monkeypatch.setattr(accept_gate, "poll", lambda *a, **k: (ERROR, ["synthetic"], {}))
    assert accept_gate.main(["--sha", SHA, "--once"]) == ERROR


# ------------------------------------------------------------------ polling


def test_poll_keeps_going_through_an_early_reject(monkeypatch):
    """A deploy is in flight: the OLD release answering is expected, not fatal."""
    seq = [(REJECT, ["old release"], {}), (ACCEPT, ["swapped"], {})]
    calls = {"n": 0}

    def fake_probe(_base, _sha):
        r = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(accept_gate, "probe_once", fake_probe)
    monkeypatch.setattr(accept_gate.time, "sleep", lambda _s: None)
    verdict, _, _ = accept_gate.poll("http://x", SHA, 60, 0, False, log=lambda *_a: None)
    assert verdict == ACCEPT
    assert calls["n"] == 2


def test_once_does_not_poll(monkeypatch):
    calls = {"n": 0}

    def fake_probe(_base, _sha):
        calls["n"] += 1
        return (REJECT, ["still v64"], {})

    monkeypatch.setattr(accept_gate, "probe_once", fake_probe)
    verdict, _, _ = accept_gate.poll("http://x", SHA, 60, 0, True, log=lambda *_a: None)
    assert verdict == REJECT
    assert calls["n"] == 1


def test_poll_returns_last_verdict_at_deadline(monkeypatch):
    monkeypatch.setattr(accept_gate, "probe_once", lambda *_a: (REJECT, ["never swapped"], {}))
    monkeypatch.setattr(accept_gate.time, "sleep", lambda _s: None)
    verdict, reasons, _ = accept_gate.poll("http://x", SHA, 0, 0, False, log=lambda *_a: None)
    assert verdict == REJECT
    assert reasons == ["never swapped"]

# ------------------------------------------------------- CLI contract: ACCEPT
#
# FU-175. The three tests above pin main()'s REJECT (1) and ERROR (2) exit
# codes. Nothing pinned ACCEPT (0) -- the ONE verdict that lets a fired deploy
# stand. evaluate() and poll() were asserted to RETURN ACCEPT, but the exit code
# is a different channel and it is the channel the one-click reads. A mutant
# that ends main() with `return REJECT` leaves every other test green and makes
# the gate structurally incapable of ever accepting: a perfectly healthy prod
# would be rolled back. Same family as #2294 (fire_gate's exit code never seen
# RED) inverted -- here it had never been seen GREEN.


def test_main_accept_verdict_is_exit_code_zero(monkeypatch):
    monkeypatch.setattr(
        accept_gate, "poll", lambda *a, **k: (ACCEPT, ["all green"], {"health_status": 200})
    )
    assert accept_gate.main(["--sha", SHA, "--once"]) == ACCEPT


def test_main_accept_is_numerically_zero_for_a_shell_caller(monkeypatch):
    """`if ($LASTEXITCODE -ne 0)` is the real caller. ACCEPT must BE 0."""
    monkeypatch.setattr(accept_gate, "poll", lambda *a, **k: (ACCEPT, ["all green"], {}))
    assert accept_gate.main(["--sha", SHA, "--once"]) == 0
    assert ACCEPT == 0


def test_main_accept_through_the_json_path_is_also_zero(monkeypatch):
    """--json returns from a DIFFERENT return statement than the human path."""
    monkeypatch.setattr(accept_gate, "poll", lambda *a, **k: (ACCEPT, ["all green"], {}))
    assert accept_gate.main(["--sha", SHA, "--once", "--json"]) == ACCEPT


@pytest.mark.parametrize("verdict", [0, 1, 2])
def test_json_exit_code_field_agrees_with_the_actual_exit_code(verdict, monkeypatch, capsys):
    """Two channels report the verdict: the JSON body and the process rc.

    A caller may parse either. They must never disagree -- a body saying
    ACCEPT beside an rc of 1 is exactly the printed-vs-returned split that
    #2294 found in fire_gate.
    """
    monkeypatch.setattr(accept_gate, "poll", lambda *a, **k: (verdict, ["synthetic"], {}))
    rc = accept_gate.main(["--sha", SHA, "--once", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == verdict
    assert payload["exit_code"] == rc
    assert payload["verdict"] == accept_gate._VERDICT_NAME[rc]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
