"""
Tests for zo_sentinel.build_completion -- the ghost-completion guard.

The bug it fixes: goose_runner marked a directive .done on process-success even
when its declared output_file was never written, permanently burning the
directive. These prove the single "did it actually build?" definition both
goose_runner (prevent) and tools/sweep_ghost_done.py (remediate) rely on.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.build_completion import (  # noqa: E402
    MIN_OUTPUT_BYTES,
    bump_ghost,
    clear_ghost,
    declared_output,
    ghost_attempts,
    output_confirmed,
)

_BODY = "x = 1\n" * 20  # comfortably over MIN_OUTPUT_BYTES


# --- output_confirmed -------------------------------------------------------

def test_no_declared_output_is_trusted(tmp_path):
    # goal/wire directive: nothing to verify -> trust process success
    assert output_confirmed({"task": "wire_x_into_y"}, home=str(tmp_path)) is True


def test_declared_output_present_confirms(tmp_path):
    (tmp_path / "tool_security_enrichment.py").write_text(_BODY, encoding="utf-8")
    d = {"task": "build_tool_security_enrichment",
         "output_file": "tool_security_enrichment.py"}
    assert output_confirmed(d, home=str(tmp_path)) is True


def test_declared_output_absent_is_ghost(tmp_path):
    # the exact regression: directive claims a file it never produced
    d = {"task": "build_tool_security_enrichment",
         "output_file": "tool_security_enrichment.py"}
    assert output_confirmed(d, home=str(tmp_path)) is False


def test_empty_output_is_ghost(tmp_path):
    (tmp_path / "stub.py").write_text("x" * (MIN_OUTPUT_BYTES - 1), encoding="utf-8")
    assert output_confirmed({"output_file": "stub.py"}, home=str(tmp_path)) is False


def test_absolute_output_path_honored(tmp_path):
    f = tmp_path / "abs.py"
    f.write_text(_BODY, encoding="utf-8")
    assert output_confirmed({"output_file": str(f)}, home="/nonexistent") is True


def test_declared_output_resolves_under_home(tmp_path):
    assert declared_output({"output_file": "a/b.py"}, home=str(tmp_path)) == tmp_path / "a/b.py"
    assert declared_output({"task": "no output"}, home=str(tmp_path)) is None


# --- ghost-attempt counter --------------------------------------------------

def test_ghost_counter_bumps_and_clears(tmp_path):
    did = "build_url_safety_enrichment"
    assert ghost_attempts(tmp_path, did) == 0
    assert bump_ghost(tmp_path, did, "2026-06-02T00:00:00Z") == 1
    assert bump_ghost(tmp_path, did, "2026-06-02T00:10:00Z") == 2
    assert ghost_attempts(tmp_path, did) == 2
    clear_ghost(tmp_path, did)
    assert ghost_attempts(tmp_path, did) == 0
