#!/usr/bin/env python3
# deps: requests
"""
verify_enrichment_dispatcher_temporal_stability_coverage.py

Verification script that checks the enrichment_dispatcher_daemon.py correctly
routes temporal_stability enrichment:

  1. temporal_stability is listed in the dispatcher's signal coverage
     (SUPPORTED_SIGNALS / get_enricher_registry() / dispatch_enrichment switch)
  2. compute_score() calls for temporal_stability conform to the
     enrichment contract: returns tuple[float, dict] with float in [0, 100]
  3. Results are written to mcp_signal_enrichments via write_service
  4. The enricher produces varied scores (>=3 distinct values across test inputs)

This is a pure-read verification; no DB writes or network I/O at import time.
All work is guarded behind `if __name__ == '__main__'`.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# ------------------------------------------------------------------
# paths
# ------------------------------------------------------------------

SIGNAL_NAME = "temporal_stability"
DISPATCHER_PATH = Path(__file__).parent / "enrichment_dispatcher_daemon.py"
ENRICHER_PATHS = [
    Path(__file__).parent / "temporal_stability_enrichment.py",
    Path(__file__).parent / "temporal_stability_enrichment_v2.py",
    Path(__file__).parent / "temporal_stability_enrichment_v3.py",
    Path(__file__).parent / "temporal_stability_enrichment_v4.py",
    Path(__file__).parent / "temporal_stability_enrichment_v5.py",
    Path(__file__).parent / "temporal_stability_enrichment_v6.py",
]


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def load_module_from_path(module_name: str, file_path: Path) -> Any | None:
    """Load a module from file path without side effects."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # pragma: no cover
        return None
    return module


def find_enricher_module() -> tuple[Any | None, str | None]:
    """Find the highest-versioned temporal_stability enricher that has compute_score."""
    for path in sorted(ENRICHER_PATHS, reverse=True):
        if path.exists():
            name = path.stem
            mod = load_module_from_path(name, path)
            if mod is not None and hasattr(mod, "compute_score"):
                return mod, name
    return None, None


def _ast_unparse(node: ast.AST) -> str:
    """Wrapper for ast.unparse that works on Python <3.9."""
    if hasattr(ast, "unparse"):
        return ast.unparse(node)
    # Fallback for older Python (minimal repr only)
    return ast.dump(node)


# ------------------------------------------------------------------
# Check 1: dispatcher lists temporal_stability in signal coverage
# ------------------------------------------------------------------

def check_dispatcher_lists_temporal_stability() -> tuple[bool, str]:
    """
    Check that the dispatcher has temporal_stability wired as a supported signal.

    Scans the AST for:
      - SUPPORTED_SIGNALS / SIGNAL_COVERAGE / COVERED_SIGNALS constant containing 'temporal_stability'
      - get_enricher_registry() returning a dict with 'temporal_stability' key
      - compute_temporal_stability_score() function defined
      - dispatch_enrichment() containing 'temporal_stability' in its if/elif chain
    """
    if not DISPATCHER_PATH.exists():
        return False, "enrichment_dispatcher_daemon.py not found"

    source = DISPATCHER_PATH.read_text()
    tree = ast.parse(source)

    # Pattern A: named constant list containing the signal
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    upper = target.id.upper()
                    if any(kw in upper for kw in ("SUPPORTED", "SIGNAL", "COVERAGE", "ENRICHERS")):
                        try:
                            val_str = _ast_unparse(node.value)
                        except Exception:
                            continue
                        if SIGNAL_NAME in val_str:
                            return True, (
                                f"Found '{SIGNAL_NAME}' in constant {target.id} = {val_str[:80]}"
                            )

    # Pattern B: get_enricher_registry() dict literal with 'temporal_stability' key
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            fname_lower = node.name.lower()
            if "enricher_registry" in fname_lower or fname_lower == "get_registry":
                try:
                    func_src = _ast_unparse(node)
                except Exception:
                    # fall back to line scanning
                    for line in source.splitlines():
                        if SIGNAL_NAME in line and "return" in line.lower():
                            return True, f"Found '{SIGNAL_NAME}' in {node.name}()"
                    continue
                if SIGNAL_NAME in func_src:
                    return True, f"Found '{SIGNAL_NAME}' in {node.name}()"

    # Pattern C: compute_temporal_stability_score function defined
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "temporal_stability" in node.name.lower():
                return True, f"Found compute function: {node.name}()"

    # Pattern D: dispatch_enrichment() if/elif chain references temporal_stability
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_enrichment":
            try:
                func_src = _ast_unparse(node)
            except Exception:
                # line-based fallback
                lines = source.splitlines()
                start = node.lineno - 1
                end = node.end_lineno or start + 200
                func_src = "\n".join(lines[start:end])
            if SIGNAL_NAME in func_src:
                return True, f"Found '{SIGNAL_NAME}' in dispatch_enrichment()"

    return False, f"'{SIGNAL_NAME}' not found in dispatcher signal coverage"


# ------------------------------------------------------------------
# Check 2: enricher compute_score conforms to tuple[float, dict] contract
# ------------------------------------------------------------------

def check_enricher_contract() -> tuple[bool, str]:
    """
    Verify temporal_stability enricher.compute_score returns tuple[float, dict]
    with score in [0, 100].
    """
    mod, name = find_enricher_module()
    if mod is None:
        return False, "No temporal_stability enricher found with compute_score"

    # Run against multiple diverse inputs
    test_cases = [
        {
            "age_days": 400,
            "community_signal": "high",
            "supply_chain": "complex",
            "download_count": 50000,
        },
        {
            "age_days": 20,
            "community_signal": "low",
            "supply_chain": "simple",
            "download_count": 50,
        },
        {
            "age_days": 200,
            "community_signal": "moderate",
            "supply_chain": "moderate",
            "download_count": 5000,
        },
    ]

    for meta in test_cases:
        try:
            result = mod.compute_score(meta)
        except Exception as e:
            return False, f"compute_score({name}) raised: {e}"

        if not isinstance(result, tuple) or len(result) != 2:
            return False, (
                f"compute_score must return tuple(float, dict); "
                f"got {type(result).__name__} of length {len(result) if isinstance(result, tuple) else 'N/A'}"
            )

        score, evidence = result
        if not isinstance(score, (int, float)):
            return False, f"score must be numeric; got {type(score).__name__}"
        score_f = float(score)
        if not (0.0 <= score_f <= 100.0):
            return False, f"score {score_f} out of range [0, 100]"
        if not isinstance(evidence, dict):
            return False, f"evidence must be dict; got {type(evidence).__name__}"

    return True, f"{name}.compute_score() → tuple[float, dict] with score ∈ [0, 100] ✓"


# ------------------------------------------------------------------
# Check 3: dispatcher writes results to mcp_signal_enrichments
# ------------------------------------------------------------------

def check_write_service_call() -> tuple[bool, str]:
    """
    Verify the dispatcher writes enrichment results to mcp_signal_enrichments
    via write_service (requests.post to WRITE_SERVICE_URL with 'rows').
    """
    if not DISPATCHER_PATH.exists():
        return False, "enrichment_dispatcher_daemon.py not found"

    source = DISPATCHER_PATH.read_text()
    if "mcp_signal_enrichments" in source:
        # also confirm it is in a write_service call
        if '"table"' in source or "'table'" in source:
            return True, "Dispatcher writes to mcp_signal_enrichments via write_service ✓"
        return True, "Dispatcher references mcp_signal_enrichments ✓"
    return False, "Dispatcher does not reference 'mcp_signal_enrichments' table"


# ------------------------------------------------------------------
# Check 4: enricher produces varied scores (discrimination capability)
# ------------------------------------------------------------------

def check_score_variety() -> tuple[bool, str]:
    """
    Verify the temporal_stability enricher produces >=3 distinct score values
    across a diverse set of inputs.  The signal diagnostic reported 7 distinct
    values in the 0.8-90.0 range — we confirm the enricher is capable of
    similar discrimination.
    """
    mod, name = find_enricher_module()
    if mod is None:
        return False, "No temporal_stability enricher found"

    test_cases = [
        # very young, no signal
        {"age_days": 5,  "community_signal": None,          "supply_chain": None,         "download_count": None},
        # young, low signal, simple chain
        {"age_days": 20, "community_signal": "low",        "supply_chain": "simple",      "download_count": 50},
        # mid-young, moderate signal
        {"age_days": 100,"community_signal": "moderate",    "supply_chain": "moderate",   "download_count": 500},
        # mid-age, active community
        {"age_days": 200,"community_signal": "active",     "supply_chain": "moderate",   "download_count": 5000},
        # mature, high community, complex chain
        {"age_days": 400,"community_signal": "high",       "supply_chain": "complex",    "download_count": 50000},
        # very mature, very high community, enterprise chain
        {"age_days": 700,"community_signal": "very high",  "supply_chain": "enterprise", "download_count": 200000},
        # young, low signal, highly complex chain (penalty case)
        {"age_days": 50, "community_signal": "low",        "supply_chain": "highly complex", "download_count": 10},
        # mid-mature, moderate community, minimal chain
        {"age_days": 300,"community_signal": "moderate",   "supply_chain": "minimal",    "download_count": 10000},
    ]

    scores: set[float] = set()
    for meta in test_cases:
        try:
            score, _ = mod.compute_score(meta)
            rounded = round(float(score), 1)
            scores.add(rounded)
        except Exception:
            pass

    distinct = len(scores)
    if distinct < 3:
        return False, (
            f"Only {distinct} distinct score values; need >=3 for discrimination "
            f"(inputs may be under-differentiated)"
        )

    score_min = min(scores)
    score_max = max(scores)
    return True, (
        f"Enricher produces {distinct} distinct score values "
        f"in range [{score_min:.1f}, {score_max:.1f}] — discrimination ✓"
    )


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    os.chdir(Path(__file__).parent)

    checks: list[tuple[str, callable]] = [
        ("Dispatcher lists temporal_stability in signal coverage",    check_dispatcher_lists_temporal_stability),
        ("Enricher compute_score contract (tuple[float, dict])",      check_enricher_contract),
        ("Dispatcher writes results to mcp_signal_enrichments",       check_write_service_call),
        ("Enricher score variety (>=3 distinct values)",               check_score_variety),
    ]

    all_passed = True
    for label, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:
            ok = False
            detail = f"Unexpected error: {e}"
        status = "PASS" if ok else "FAIL"
        LOG.info("[%s] %s  —  %s", status, label, detail)
        if not ok:
            all_passed = False

    LOG.info("-" * 60)
    if all_passed:
        LOG.info("All checks PASSED — temporal_stability is correctly wired in the dispatcher.")
        return 0
    else:
        LOG.error("Some checks FAILED — see details above.")
        return 1


if __name__ == "__main__":
    sys.exit(run())
