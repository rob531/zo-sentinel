"""FU-040: the gaps map must not harvest Exemplar references as build targets.

2026-07-20: the PHASE 9 refill specified 6 targets; the live gaps map returned 7.
The extra was schema_prm_guard.py, which exists nowhere in the repo and appears
in PRODUCT_SPEC.md ONLY as the Exemplar for edit_class_directive_validator.py.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from directive_knowledge_sources import _spec_candidate_files, _strip_exemplar_refs

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_exemplar_reference_is_not_a_candidate():
    line = "- directive candidate: `edit_class_directive_validator.py` -- pure validator. Exemplar: `schema_prm_guard.py`. ACCEPTANCE: prints PASS."
    got = _spec_candidate_files(line)
    assert "edit_class_directive_validator.py" in got
    assert "schema_prm_guard.py" not in got


def test_plural_exemplars_also_excluded():
    line = "- directive candidate: `server_cve_search_api.py` -- CVE search. Exemplars: vuln_links_query_api.py, vuln_exposure_rollup_api.py. ACCEPTANCE: prints PASS."
    got = _spec_candidate_files(line)
    assert got == ["server_cve_search_api.py"], got


def test_target_before_marker_is_never_dropped():
    # the safety property the fix relies on: the target always precedes Exemplar:
    assert _strip_exemplar_refs("a `x.py` Exemplar: `y.py`").strip() == "a `x.py`"
    assert _strip_exemplar_refs("no marker here x.py") == "no marker here x.py"


def test_multiline_window_still_scanned():
    spec = "\n".join([
        "**Retention / lifecycle daemons (NOT YET BUILT):**",
        "- `retention_sweeper.py` -- age-based expiry",
        "- `exemption_expirer.py` -- nightly check",
    ])
    got = _spec_candidate_files(spec)
    assert "retention_sweeper.py" in got
    assert "exemption_expirer.py" in got


def test_live_spec_yields_the_six_phase9_targets_and_not_the_exemplar():
    spec = (REPO / "PRODUCT_SPEC.md").read_text(encoding="utf-8")
    got = _spec_candidate_files(spec)
    for name in [
        "factory_liveness_continuity_probe.py",
        "build_gate_attribution_report.py",
        "edit_class_directive_validator.py",
        "unmounted_router_census_report.py",
        "ghost_retry_burn_report.py",
        "daemon_roster_coverage_report.py",
    ]:
        assert name in got, f"PHASE 9 target lost: {name}"
    assert "schema_prm_guard.py" not in got, "exemplar still harvested as a target"


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
