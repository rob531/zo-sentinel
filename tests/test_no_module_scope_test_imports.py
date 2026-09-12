"""No module under services/active/ may import a test-only dependency at MODULE SCOPE.

WHY THIS EXISTS (measured, 2026-09-09/10):
  prod release v86 was FIRED on a736470, REJECTED by accept_gate, and rolled back
  to v87 because services/active/org_risk_summary/logic.py carried
  `from fastapi.testclient import TestClient` at module scope. TestClient is a
  test-only surface; importing it at module scope drags a test dependency onto the
  prod spine's import path, and the spine fails to come up.

  PR #4858 cured ONE file. A census then found THREE instances across two services
  -- one of which (cadence_job_sla_report) nobody had named at all. One door of
  three is not a cure, so this test is the latch: it fails on ANY door.

THE CONTROL (why this test is not a rubber stamp): it was run against
origin/main BEFORE the cure and reported 2 offenders; after the cure, 0. A probe
that has never been observed RED is unproven, not passing.
"""
import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIVE = REPO_ROOT / "services" / "active"

# Substrings that mark an import as test-only. Keyed on the SYMBOL/submodule path,
# not the top-level package: `fastapi` is a prod dependency but
# `fastapi.testclient` is not, and a top-level-package check misses it entirely.
TEST_ONLY_NEEDLES = (
    "testclient",
    "pytest",
    "hypothesis",
    "unittest.mock",
    "freezegun",
    "responses.",
    "moto",
    "faker",
    "testcontainers",
    "factory_boy",
)


def _module_scope_imports(tree):
    """Only direct children of Module. An import inside a function or an
    `if __name__ == "__main__":` guard is fine -- it never runs on the spine."""
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node.lineno, f"{node.module or ''}.{alias.name}"


def _active_modules():
    if not ACTIVE.is_dir():
        return []
    out = []
    for path in sorted(ACTIVE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        if "tests" in path.parts:
            continue
        out.append(path)
    return out


def test_census_is_not_vacuous():
    """R3: a bucket that goes to ZERO must prove the check RAN.

    If services/active/ is empty or unreadable, the offender test below passes
    for free and tells nobody. Fail loudly instead."""
    mods = _active_modules()
    assert mods, (
        "census scanned 0 modules under services/active/ -- this test would pass "
        "vacuously. Check the path, do not delete the assertion."
    )


def test_no_module_scope_test_only_imports():
    offenders = []
    unparsed = []
    for path in _active_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            unparsed.append(f"{rel}: {exc}")
            continue
        for lineno, name in _module_scope_imports(tree):
            low = name.lower()
            if any(needle in low for needle in TEST_ONLY_NEEDLES):
                offenders.append(f"{rel}:{lineno}  {name}")

    # An unparsable module is UNKNOWN, and unknown is not zero (R6).
    assert not unparsed, "could not parse (result is UNKNOWN, not clean):\n  " + "\n  ".join(unparsed)

    assert not offenders, (
        "module-scope test-only import(s) under services/active/ -- these break the "
        "prod spine at import time (this is what rolled back release v86):\n  "
        + "\n  ".join(offenders)
        + "\n\nFix: move the import inside the function or the "
          '`if __name__ == \"__main__\":` guard that actually uses it.'
    )
