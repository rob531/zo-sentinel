#!/usr/bin/env python3
"""
verify_commit2_guards.py -- non-destructive self-tests for commit 2.

Two tests:
  Test A: Protected files are never quarantined.
  Test B: Validator blocks quarantined rebuilds.

Both snapshot the state file BEFORE modifying and restore it at the end.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/home/workspace/zo_sentinel")
sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")

STATE_FILE = Path("/home/workspace/zo_sentinel/gate_quality_state.json")
SNAPSHOT   = Path("/home/workspace/zo_sentinel/gate_quality_state.verify_backup.json")
SENTINEL_DIR = Path("/home/workspace/zo_sentinel")


def _backup_state():
    if STATE_FILE.exists():
        shutil.copy2(STATE_FILE, SNAPSHOT)
        print(f"  [backup] state file -> {SNAPSHOT.name}")


def _restore_state():
    if SNAPSHOT.exists():
        shutil.copy2(SNAPSHOT, STATE_FILE)
        SNAPSHOT.unlink()
        print(f"  [restore] state file restored from {SNAPSHOT.name}")
    else:
        print("  [warn] no snapshot to restore from")


def _pick_protected_target():
    """Find a protected file that actually exists on disk at the sentinel
    path. Returns filename or None if none of the protected set are here.
    Tries ui_server.py first because it's our most reliable fixture."""
    from gate_8_new_module import GATE8_PROTECTED_FILES
    preferred_order = [
        "ui_server.py",
        "registry_api.py",
        "full_schema_bootstrap.py",
        "signal_analyser.py",
        "trust_synthesiser.py",
        "attestation_engine.py",
        "rug_pull_monitor.py",
        "dashboard.html",
        "sentinel_status.html",
    ]
    for candidate in preferred_order:
        if candidate in GATE8_PROTECTED_FILES and (SENTINEL_DIR / candidate).exists():
            return candidate
    # Fallback: any protected file that happens to be on disk
    for fn in GATE8_PROTECTED_FILES:
        if (SENTINEL_DIR / fn).exists():
            return fn
    return None


def test_a_protected_invulnerable():
    print("\n=== TEST A: protected files never quarantined ===")
    import gate_quality_state as gqs

    target_file = _pick_protected_target()
    if target_file is None:
        print("  [SKIP] no protected file found on disk in sentinel dir")
        return None

    print(f"  testing with protected target: {target_file}")
    on_disk = SENTINEL_DIR / target_file
    quarantine_dir = SENTINEL_DIR / "quarantine"
    quarantined_candidate = quarantine_dir / target_file

    was_in_quarantine_before = quarantined_candidate.exists()

    # Inject 99 failures
    with gqs._LockedStateFile() as data:
        data["file_retries"][target_file] = {
            "attempts": 99,
            "last_failed_at": "2026-04-18T22:00:00+00:00",
            "last_error": "synthetic test: pretend this failed many times",
            "cohorts": ["verify_test_cohort"],
        }
    print(f"  injected attempts=99 for {target_file}")

    from gate_8_new_module import Gate8NewModule, GATE8_PROTECTED_FILES

    if target_file not in GATE8_PROTECTED_FILES:
        print(f"  [FAIL-SETUP] {target_file} not in GATE8_PROTECTED_FILES; test invalid")
        return False

    # Build a bare instance; bypass __init__'s db arg
    gate = Gate8NewModule.__new__(Gate8NewModule)
    gate.db = None
    gate.run_id = "verify_test"
    gate.failures = 0
    gate.checks = 0
    gate._cohort_totals = {}
    gate._files_this_run_ok = set()
    gate._files_this_run_bad = {}

    def _noop_check(check_name, condition, **kwargs):
        return condition
    gate.check = _noop_check

    try:
        gate._quarantine_overdue("verify_test_cohort")
    except Exception as e:
        print(f"  [FAIL] _quarantine_overdue raised: {type(e).__name__}: {e}")
        return False

    still_on_disk = on_disk.exists()
    moved_to_quarantine = (quarantined_candidate.exists() and not was_in_quarantine_before)

    if still_on_disk and not moved_to_quarantine:
        print(f"  [PASS] {target_file} stayed in place despite attempts=99")
        snap = gqs.snapshot()
        if target_file in snap.get("quarantined", {}):
            print(f"  [FAIL] state file records it as quarantined (inconsistent)")
            return False
        return True
    else:
        print(f"  [FAIL] protection bypassed:")
        print(f"    still_on_disk={still_on_disk}")
        print(f"    moved_to_quarantine={moved_to_quarantine}")
        # Attempt to recover the file if somehow moved
        if moved_to_quarantine:
            try:
                quarantined_candidate.rename(on_disk)
                print(f"    [recovered] {target_file} moved back to sentinel dir")
            except Exception as e:
                print(f"    [RECOVERY FAIL] could not move back: {e}")
        return False


def test_b_validator_blocks_quarantined():
    print("\n=== TEST B: validator rejects directives for quarantined files ===")
    import gate_quality_state as gqs

    fake_file = "fake_test_for_validator.py"

    with gqs._LockedStateFile() as data:
        data["quarantined"][fake_file] = {
            "quarantined_at": "2026-04-18T22:00:00+00:00",
            "reason": "synthetic test: never really quarantined",
            "attempts_when_quarantined": 3,
        }
    print(f"  injected quarantine entry for {fake_file}")

    from sentinel_directive_generator import validate_directive

    directive = {
        "task": "build_fake_test_for_validator",
        "handler": "generate_file",
        "output_file": fake_file,
        "complexity": "medium",
        "description": "This is a long-enough synthetic description intended "
                       "to pass the 50-char minimum check and isolate the "
                       "quarantine rejection path for testing.",
    }

    ok, reason = validate_directive(directive)
    print(f"  validator returned: ok={ok}, reason={reason!r}")

    if ok:
        print(f"  [FAIL] validator accepted a directive for a quarantined file")
        return False
    if "quality gate blocks rebuild" not in reason and "quarantined" not in reason:
        print(f"  [FAIL] rejection reason doesn't mention quarantine/quality gate")
        return False
    print(f"  [PASS] validator correctly rejected")
    return True


def main():
    if not STATE_FILE.exists():
        import gate_quality_state as gqs  # noqa
        _ = gqs.snapshot()

    if not STATE_FILE.exists():
        print("[FAIL-SETUP] state file could not be created")
        return 2

    _backup_state()
    try:
        a_result = test_a_protected_invulnerable()
        b_result = test_b_validator_blocks_quarantined()
    finally:
        _restore_state()

    print("\n=== SUMMARY ===")
    print(f"  Test A (protected invulnerable): {'PASS' if a_result else 'SKIP' if a_result is None else 'FAIL'}")
    print(f"  Test B (validator blocks):       {'PASS' if b_result else 'FAIL'}")

    failures = sum(1 for r in (a_result, b_result) if r is False)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())