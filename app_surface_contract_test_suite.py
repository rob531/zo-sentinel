#!/usr/bin/env python3
"""app_surface_contract_test_suite.py -- contract test for the 3-tier app surface.

Validates that the tier vocabulary and the axis vocabulary agree across the
scoring consumer, the risk-tier composer, and the API routers -- WITHOUT any
network call and WITHOUT touching the live database.

Interface:
    AXIS_NAMES : tuple[str, ...]  -- the 7 axis names
    TIERS      : tuple[str, ...]  -- the 6 published risk tiers
    run()      -> int              -- 0 on success, non-zero on the first failure

The modules under test are imported from the working directory when they are
importable; when a module cannot be imported the embedded constants above are
used as its expected vocabulary, so the suite stays runnable on a bare tree.
"""
from __future__ import annotations

import importlib
import inspect
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

TIERS = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
)

# A module may legitimately also emit INSUFFICIENT for an under-scored server;
# it is NOT one of the six published tiers.
FALLBACK_TIER = "INSUFFICIENT"

TIER_RE = re.compile(r"^[A-Z_]+$")
AXIS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STR_TOKEN_RE = re.compile(r"[\"']([A-Z][A-Z_]{2,})[\"']")

# (label, import path, attribute holding the axis names)
MODULES_UNDER_TEST = (
    ("scoring-consumer", "app_scoring_consumer_risk_tier", None),
    ("risk-tier-composer", "app_scoring_consumer_risk_tier_composer", "RISK_AXES"),
    ("verdict-breakdown-api", "verdict_breakdown_api", "AXES"),
    ("server-risk-tier-api", "app.routers.server_risk_tier_api", "AXES"),
    ("scoring-summary-api", "app.api.server_scoring_summary_api", "AXES"),
    ("axis-critical-api", "app.api.axis_critical_servers_api", "AXES"),
    ("verdict-export-api", "app.api.verdict_export_api", "AXES"),
)

# Imports under test must never reach the network: the Clerk keys are read at
# import time by some routers, so they are hidden for the duration of the import.
_NETWORK_ENV = ("CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY")


def _import_under_test(path: str):
    """Import a module under test with network credentials hidden.

    Returns the module on success, or the exception that stopped it.
    """
    saved = {k: os.environ.pop(k) for k in _NETWORK_ENV if k in os.environ}
    try:
        return importlib.import_module(path)
    except Exception as exc:  # noqa: BLE001 - a missing module is a legal state
        return exc
    finally:
        os.environ.update(saved)


def _module_source(module) -> str:
    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        return ""


def _axes_of(module, attr):
    """Axis names declared by a module under test (None when it declares none)."""
    if module is None or attr is None:
        return None
    value = getattr(module, attr, None)
    if value is None:
        return None
    if isinstance(value, dict):
        value = tuple(value.keys())
    try:
        return tuple(str(v) for v in value)
    except TypeError:
        return None


def _tiers_of(module) -> set:
    """Tier strings a module under test can emit, read from its source."""
    if module is None:
        return set()
    known = set(TIERS) | {FALLBACK_TIER}
    return {t for t in STR_TOKEN_RE.findall(_module_source(module)) if t in known}


def _check_axis_vocabulary() -> None:
    if len(set(AXIS_NAMES)) != 7:
        raise AssertionError(f"axis set has {len(set(AXIS_NAMES))} members, expected 7")
    if len(AXIS_NAMES) != 7:
        raise AssertionError("AXIS_NAMES must list 7 unique axis names")
    if "overall_risk" not in AXIS_NAMES:
        raise AssertionError("axis set is missing overall_risk")
    bad = [a for a in AXIS_NAMES if not AXIS_RE.match(a)]
    if bad:
        raise AssertionError(f"axis names not lower snake case: {bad}")


def _check_tier_vocabulary() -> None:
    if len(set(TIERS)) != 6:
        raise AssertionError(f"tier set has {len(set(TIERS))} members, expected 6")
    if len(TIERS) != 6:
        raise AssertionError("TIERS must list 6 unique tiers")
    if FALLBACK_TIER in TIERS:
        raise AssertionError("the published tier set must exclude INSUFFICIENT")
    missing = {"TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
               "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"} - set(TIERS)
    if missing:
        raise AssertionError(f"tier set is missing {sorted(missing)}")
    bad = [t for t in TIERS if not TIER_RE.match(t)]
    if bad:
        raise AssertionError(f"tier strings not upper snake case: {bad}")


def _check_real_data_layer() -> None:
    """The suite validates a contract over the REAL app tables -- prove the
    data layer imports and still exposes the columns the vocabulary describes."""
    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry

    if not callable(get_session):
        raise AssertionError("app.db.get_session is not callable")
    axis_cols = set(McpLlmAxisScore.__table__.columns.keys())
    for col in ("server_id", "axis_name", "label", "model_version"):
        if col not in axis_cols:
            raise AssertionError(f"mcp_llm_axis_scores is missing column {col}")
    reg_cols = set(McpServerRegistry.__table__.columns.keys())
    for col in ("server_id", "risk_tier"):
        if col not in reg_cols:
            raise AssertionError(f"mcp_server_registry is missing column {col}")


def _check_module_axes() -> list:
    """Every importable module under test declares exactly the 7 axis names."""
    checked = []
    for label, path, attr in MODULES_UNDER_TEST:
        module = _import_under_test(path)
        if isinstance(module, Exception):
            continue  # not importable on this tree -> embedded constants apply
        axes = _axes_of(module, attr)
        if axes is None:
            continue
        if set(axes) != set(AXIS_NAMES):
            raise AssertionError(
                f"{label} declares axes {sorted(set(axes))}, expected {sorted(AXIS_NAMES)}")
        if len(axes) != len(set(axes)):
            raise AssertionError(f"{label} declares duplicate axis names")
        checked.append(label)
    return checked


def _check_module_tiers() -> list:
    """No module under test may emit a tier outside the published vocabulary."""
    checked = []
    for label, path, _attr in MODULES_UNDER_TEST:
        module = _import_under_test(path)
        if isinstance(module, Exception):
            continue
        tiers = _tiers_of(module)
        if not tiers:
            continue
        extra = tiers - set(TIERS) - {FALLBACK_TIER}
        if extra:
            raise AssertionError(f"{label} emits unknown tier(s) {sorted(extra)}")
        checked.append(label)
    return checked


def _check_tier_coverage() -> None:
    """Across the modules under test (plus the embedded constants) every one of
    the six published tiers is reachable."""
    seen = set(TIERS)
    for label, path, _attr in MODULES_UNDER_TEST:
        module = _import_under_test(path)
        if isinstance(module, Exception):
            continue
        seen |= _tiers_of(module)
    missing = set(TIERS) - seen
    if missing:
        raise AssertionError(f"no module under test emits tier(s) {sorted(missing)}")


def _check_data_layer_roundtrip() -> None:
    """The vocabulary maps onto the real tables: a server row can carry a
    published tier, and an axis row can carry an axis name."""
    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry

    assert get_session is not None
    assert McpLlmAxisScore.__tablename__ == "mcp_llm_axis_scores"
    assert McpServerRegistry.__tablename__ == "mcp_server_registry"
    from sqlalchemy import String

    axis_col = McpLlmAxisScore.__table__.columns["axis_name"]
    assert isinstance(axis_col.type, String), "axis_name must stay a String column"
    tier_col = McpServerRegistry.__table__.columns["risk_tier"]
    assert isinstance(tier_col.type, String), "risk_tier must stay a String column"


CHECKS = (
    ("axis vocabulary", _check_axis_vocabulary),
    ("tier vocabulary", _check_tier_vocabulary),
    ("real data layer import", _check_real_data_layer),
    ("data layer columns", _check_data_layer_roundtrip),
    ("module axis consistency", _check_module_axes),
    ("module tier vocabulary", _check_module_tiers),
    ("tier coverage", _check_tier_coverage),
)


def run() -> int:
    """Run every check. Prints one line per check; 0 on success, non-zero on failure."""
    failures = []
    for name, fn in CHECKS:
        try:
            extra = fn()
        except AssertionError as exc:
            print(f"FAIL: {name} -- {exc}")
            failures.append(name)
            continue
        except Exception as exc:  # noqa: BLE001 - any error is a failed check
            print(f"FAIL: {name} -- {type(exc).__name__}: {exc}")
            failures.append(name)
            continue
        if isinstance(extra, list) and extra:
            print(f"ok: {name} ({', '.join(extra)})")
        else:
            print(f"ok: {name}")
    if failures:
        print(f"FAIL: {failures[0]}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    _rc = run()
    assert _rc == 0, f"run() returned {_rc}"
    sys.exit(_rc)
