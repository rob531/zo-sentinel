"""FU-032: starvation-floor allowlist gap + drift-blind exhaustion message.

(1) `_writer` (and 9 sibling role words) were missing from _FLOOR_ROLE_SUFFIXES,
    so chairman-spec'd PHASE 8b target score_run_ledger_writer.py was invisible
    to the seeding path purely on name shape.
(2) The exhaustion message asserted "extend PRODUCT_SPEC" on 2026-07-20 when the
    spec HAD been extended 17h earlier and the runtime clone was 22 commits
    behind. The floor could not tell a spent anchor from a stale checkout.
"""
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = (REPO / "zo_sentinel" / "sentinel_directive_generator_goose.py").read_text(encoding="utf-8")

_ns = {"subprocess": subprocess, "SENTINEL_DIR": REPO}
for _blk in ("_FLOOR_ROLE_SUFFIXES", "_FLOOR_TASK_PREFIXES", "_FLOOR_DEPRECATED_SUBSTR"):
    exec(re.search(_blk + r" = \(.*?\n\)", SRC, re.S).group(0), _ns)
exec(re.search(r"def _is_seedworthy.*?\n    return False.*?\n", SRC, re.S).group(0), _ns)
exec(re.search(r"def _checkout_drift_note.*?\n\n\ndef ", SRC, re.S).group(0)[:-6], _ns)
seedworthy = _ns["_is_seedworthy"]
drift_note = _ns["_checkout_drift_note"]


def test_writer_suffix_is_seedworthy():
    # the exact case that escaped on 2026-07-20
    assert seedworthy("score_run_ledger_writer.py") is True
    assert seedworthy("run_reconciliation_report.py") is True


def test_audited_asked_suffixes_are_seedworthy():
    for f in [
        "app_router_registry.py", "family_first_wave_planner.py",
        "org_api_key_manager.py", "perspective_email_digest.py",
        "perspective_model.py", "tenant_org_model.py", "product_audit_log.py",
        "rbac_enforcer.py", "score_results_push_verifier.py",
        "trust_gating_override.py",
    ]:
        assert seedworthy(f) is True, f


def test_out_of_scope_modules_stay_unseedworthy():
    # PRODUCT_SPEC lists these under NOT IN SCOPE / dormant; the floor must
    # never be able to seed them, so their role words stay off the allowlist.
    for f in ["graphql_schema_builder.py", "incident_webhook_dispatcher.py",
              "pattern_learner.py"]:
        assert seedworthy(f) is False, f


def test_spine_and_paid_launch_scripts_stay_unseedworthy():
    for f in ["main.py", "fire_score.py", "finalize_score.py"]:
        assert seedworthy(f) is False, f


def test_junk_still_rejected():
    for f in ["settings.py", "foo_bar.py", "snake_case.py", "utils.py",
              "overview_dashboard_view.html"]:
        assert seedworthy(f) is False, f


def test_every_asked_spec_target_is_now_visible():
    spec = (REPO / "PRODUCT_SPEC.md").read_text(encoding="utf-8")
    asked = set(re.findall(r"^- directive candidate: .?([a-z][a-z0-9_]{2,40}\.py)", spec, re.M))
    invisible = sorted(f for f in asked if not seedworthy(f))
    # _v2 version tags are not role words; they are the known remainder.
    assert invisible == ["ask_query_expansion_v2.py", "cve_facet_compile_wiring_v2.py"], invisible


def test_drift_note_reports_current_when_head_matches():
    note = drift_note()
    assert note.startswith("CHECKOUT:"), note
    assert any(k in note for k in ("IS current", "behind by", "UNREACHABLE", "UNKNOWN")), note


def test_drift_note_never_raises_outside_a_repo(tmp_path=None):
    import tempfile
    ns = dict(_ns)
    with tempfile.TemporaryDirectory() as d:
        ns2 = {"subprocess": subprocess, "SENTINEL_DIR": pathlib.Path(d)}
        exec(re.search(r"def _checkout_drift_note.*?\n\n\ndef ", SRC, re.S).group(0)[:-6], ns2)
        note = ns2["_checkout_drift_note"]()
    assert note.startswith("CHECKOUT: UNKNOWN"), note


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print("PASS" if not fails else f"FAIL ({fails})")
    sys.exit(1 if fails else 0)
