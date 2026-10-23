"""The BOOT PROOF fold in tools/fire_gate.py.

Guards the asymmetry, in both directions. Written because the thing this fold
exists to catch -- v97, 2026-09-22 -- passed every static gate in the repo and
had a Dockerfile blob byte-identical to the last successful build.

The RED direction is named explicitly: if `test_unattested_turns_safe_into_restage`
can be deleted and everything else still passes, the fold is a rubber stamp.
"""
import importlib.util
import pathlib
import sys

import pytest

_FG = pathlib.Path(__file__).resolve().parents[1] / "tools" / "fire_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("fire_gate_for_boot_tests", _FG)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fire_gate_for_boot_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fg():
    return _load()


def test_unattested_turns_safe_into_restage(fg):
    """THE RED DIRECTION. rc=1 is a DEFINITE reading -- the probe positively
    established that no successful deploy-compat run covers this image surface --
    so it blocks, and the remediation it prints is $0 and ~50s."""
    assert fg.apply_boot("SAFE", {"rc": 1}) == ("RESTAGE", True)


def test_proven_leaves_safe_alone(fg):
    assert fg.apply_boot("SAFE", {"rc": 0}) == ("SAFE", False)


def test_unknown_is_not_red(fg):
    """R6. No gh, no runs in the window, no Dockerfile -> UNKNOWN. An instrument
    that cannot reach GitHub must not convert into a blocker (R7)."""
    assert fg.apply_boot("SAFE", {"rc": 2}) == ("SAFE", False)


def test_skipped_is_not_red_and_is_not_a_pass(fg):
    """--no-boot-proof. The verdict is unchanged and the output says it skipped;
    a skip is never reported as a pass (R3)."""
    assert fg.apply_boot("SAFE", {"rc": None}) == ("SAFE", False)


def test_boot_never_relaxes_an_existing_restage(fg):
    """The fold only ever tightens. A RESTAGE from the image-surface or CI checks
    stays RESTAGE, and is not attributed to the boot probe."""
    for rc in (0, 1, 2, None):
        assert fg.apply_boot("RESTAGE", {"rc": rc}) == ("RESTAGE", False)


def test_probe_resolves_or_reports_unavailable_never_a_pass(fg):
    """Absent probe -> UNKNOWN with source 'unavailable'. Never PROVEN."""
    probe = fg.resolve_boot_proof()
    assert probe is None or probe.is_file()
    fg.BOOT_PROOF_PATHS = (pathlib.Path("/nonexistent/boot_proof.py"),)
    try:
        out = fg.boot_proof("/nonexistent", "0" * 40)
    finally:
        pass
    assert out["verdict"] == "UNKNOWN"
    assert out["rc"] == 2
    assert out["source"] == "unavailable"


def test_the_fold_is_wired_into_main_not_merely_defined(fg):
    """A pure function nobody calls is the exact defect this PR closes: boot_proof.py
    sat with ZERO callers on the fire path while recording its own gap. Assert the
    CALL SITE exists, not just the definition."""
    src = _FG.read_text(encoding="utf-8")
    assert "apply_boot(verdict, boot)" in src
    assert "--no-boot-proof" in src
