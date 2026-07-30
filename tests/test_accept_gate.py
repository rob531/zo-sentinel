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


# The four active services that declare no router in real prod (FU-114). The
# fixture carries them because the LIVE payload carries them: the pre-2026-07-30
# fixture had no `mounted` and no `skipped_no_router` key at all, so 28 tests ran
# against a payload shape prod has never emitted -- and the ACCEPT line's claim of
# "31 services mounted" could not be contradicted by an input that omitted the
# only field able to contradict it.
INERT = [
    "entity_report_exporter",
    "org_api_key_manager",
    "overview_dashboard_api",
    "verdict_watchlist_service",
]


def _spine(ok=True, failures=None, status=200, service_count=31, inert=None, mounted=None):
    """A payload shaped like the one prod actually serves.

    mounted + skipped_no_router + failures == service_count, as measured live on
    v65 at 2026-07-30T01:50Z (27 + 4 + 0 == 31). Pass `mounted` explicitly to
    build a payload whose buckets deliberately do NOT sum.
    """
    failures = failures or []
    inert = INERT if inert is None else inert
    if mounted is None:
        n = max(0, service_count - len(inert) - len(failures))
        mounted = ["svc_%02d" % i for i in range(n)]
    return (
        status,
        {
            "ok": ok,
            "service_count": service_count,
            "mounted": mounted,
            "skipped_no_router": list(inert),
            "failures": failures,
        },
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


# ------------------------------------------------- FU-114: the bucket arithmetic
#
# The live v65 payload, copied verbatim from https://mcprisky.io/spine/health at
# 2026-07-30T01:50Z. 27 mounted + 4 skipped_no_router + 0 failures == 31 declared.
# Before 2026-07-30 this gate's ACCEPT line read "(31 services mounted)" -- it
# printed service_count and called it the mounted count -- and its caveat
# hardcoded "Four of the 31 ... mount clean". Both survived 28 tests because the
# fixture above did not carry the two keys that disprove them.
#
# Every assertion in this section was seen RED against the pre-fix module.

LIVE_V65_SPINE = {
    "ok": True,
    "service_count": 31,
    "mounted": [
        "ask_answer_api", "ask_corpus_indexer", "cadence_admin_api", "config_scan_api",
        "dashboard_summary_api", "facet_enum_service", "freshness_metadata_api",
        "freshness_policy_api", "media_assets", "org_entity_search_api", "otx_threat_refs",
        "perspective_admin_api", "perspective_diff_service", "perspective_query_api",
        "runtime_deploy_info_endpoint", "score_dispute_api", "scoring_freshness_surface",
        "server_axis_scores_summary_router", "server_compare_api", "threat_intel_summary_api",
        "verdict_breakdown_api", "vuln_coverage_sla_api", "vuln_exposure_api",
        "vuln_facet_extension", "vuln_osv_ingestor", "vuln_pkg_enricher",
        "vuln_registry_linker",
    ],
    "skipped_no_router": [
        "entity_report_exporter", "org_api_key_manager", "overview_dashboard_api",
        "verdict_watchlist_service",
    ],
    "failures": [],
}


def test_the_live_payload_has_the_shape_the_fixture_claims():
    """Guards the fixture itself: 27 + 4 + 0 == 31 in the REAL payload."""
    assert len(LIVE_V65_SPINE["mounted"]) == 27
    assert len(LIVE_V65_SPINE["skipped_no_router"]) == 4
    assert LIVE_V65_SPINE["service_count"] == 27 + 4 + 0


def test_live_payload_is_never_described_as_31_mounted():
    """The exact wrong number the old ACCEPT line printed against real prod."""
    verdict, reasons = accept_gate.evaluate(_health(), _version(), (200, LIVE_V65_SPINE), SHA)
    assert verdict == ACCEPT
    assert "27 of 31 mounted" in reasons[0]
    assert "31 services mounted" not in reasons[0]


def test_buckets_decompose_the_live_payload():
    b = accept_gate.spine_buckets(LIVE_V65_SPINE)
    assert b["declared"] == 31
    assert b["mounted"] == 27
    assert b["inert"] == 4
    assert b["failed"] == 0
    assert b["unaccounted"] == 0
    assert "overview_dashboard_api" in b["inert_names"]


def test_a_missing_mounted_key_is_unknown_not_zero():
    """R6. An older prod that omits the bucket has not mounted NOTHING."""
    b = accept_gate.spine_buckets({"ok": True, "service_count": 31, "failures": []})
    assert b["mounted"] is None
    assert b["inert"] is None
    assert b["unaccounted"] is None
    text = accept_gate.describe_buckets(b)
    assert "UNKNOWN" in text
    assert "0 of 31 mounted" not in text


def test_buckets_that_do_not_sum_are_reported_loudly():
    body = dict(LIVE_V65_SPINE, mounted=LIVE_V65_SPINE["mounted"][:20])
    b = accept_gate.spine_buckets(body)
    assert b["unaccounted"] == 7
    note = accept_gate.arithmetic_note(b)
    assert note is not None and "UNACCOUNTED" in note and "7" in note


def test_an_unaccounted_remainder_does_not_change_the_verdict():
    """A reporting-layer remainder must never roll back a healthy prod."""
    body = dict(LIVE_V65_SPINE, mounted=LIVE_V65_SPINE["mounted"][:20])
    verdict, reasons = accept_gate.evaluate(_health(), _version(), (200, body), SHA)
    assert verdict == ACCEPT
    assert any("UNACCOUNTED" in r for r in reasons)


def test_buckets_that_sum_emit_no_arithmetic_note():
    """The note's negative control: it must be silent when the sum is right."""
    assert accept_gate.arithmetic_note(accept_gate.spine_buckets(LIVE_V65_SPINE)) is None
    verdict, reasons = accept_gate.evaluate(_health(), _version(), (200, LIVE_V65_SPINE), SHA)
    assert verdict == ACCEPT
    assert not any("UNACCOUNTED" in r for r in reasons)


def test_observed_reports_declared_and_mounted_as_DIFFERENT_numbers(monkeypatch):
    """prod_deploy_state.json recorded mounted_count 31 by reading service_count."""
    monkeypatch.setattr(
        accept_gate,
        "_fetch",
        lambda url: (
            (200, LIVE_V65_SPINE) if url.endswith("/spine/health")
            else _version()[1] and (200, _version()[1]) if url.endswith("/version")
            else (200, _health()[1])
        ),
    )
    verdict, _reasons, observed = accept_gate.probe_once("http://x", SHA)
    assert verdict == ACCEPT
    assert observed["spine_service_count"] == 31
    assert observed["spine_mounted_count"] == 27
    assert observed["spine_inert_count"] == 4
    assert observed["spine_unaccounted_count"] == 0
    assert len(observed["spine_inert_services"]) == 4


def test_caveat_counts_from_the_payload_instead_of_saying_four():
    text = accept_gate._accept_caveat({
        "spine_inert_count": 5,
        "spine_service_count": 32,
        "spine_mounted_count": 27,
        "spine_inert_services": ["a", "b", "c", "d", "e"],
    })
    assert "5 of 32" in text
    assert "Four" not in text


def test_caveat_stops_claiming_inert_services_mount_clean():
    text = accept_gate._accept_caveat({
        "spine_inert_count": 4,
        "spine_service_count": 31,
        "spine_mounted_count": 27,
        "spine_inert_services": LIVE_V65_SPINE["skipped_no_router"],
    })
    assert "NOT as mounted" in text
    assert "mounted is 27" in text
    assert "mount clean" not in text
    assert "overview_dashboard_api" in text


def test_caveat_with_no_inert_bucket_says_unknown_not_zero():
    text = accept_gate._accept_caveat({"spine_inert_count": None, "spine_service_count": 31})
    assert "UNKNOWN" in text
    assert "not zero" in text


def test_caveat_with_zero_inert_still_refuses_to_promise_traffic():
    text = accept_gate._accept_caveat({
        "spine_inert_count": 0, "spine_service_count": 31,
        "spine_mounted_count": 31, "spine_inert_services": [],
    })
    assert "not serves traffic" in text


def test_caveat_never_raises_on_an_observed_dict_with_no_spine_keys():
    """main() prints the caveat from whatever poll() returned, which may be bare."""
    assert "CAVEAT (FU-114)" in accept_gate._accept_caveat({})


def test_failures_still_reject_with_the_real_payload_shape():
    """The verdict semantics are UNCHANGED by this reporting fix."""
    verdict, reasons = accept_gate.evaluate(
        _health(), _version(), _spine(ok=False, failures=[_fail("boom")]), SHA
    )
    assert verdict == REJECT
    assert any("boom" in r for r in reasons)
