#!/usr/bin/env python3
"""
verify_enrichment_dispatcher_coverage.py

Static diagnostic: verify that enrichment_dispatcher_daemon.py imports and dispatches
ALL built enrichment modules.

Target modules:
  - supply_chain_enrichment
  - community_signal_enrichment
  - context_efficiency_enrichment
  - known_bad_pattern_enrichment
  - tool_count_enrichment

This is a read-only diagnostic: no DB writes, no network, stdlib only.
"""

import os
import re
import sys
from pathlib import Path

# Constants
REPO_ROOT = Path("/home/workspace/zo_sentinel")
DISPATCHER_PATH = REPO_ROOT / "enrichment_dispatcher_daemon.py"

# The 5 enrichment modules that must be wired
REQUIRED_ENRICHMENTS = {
    "supply_chain": "supply_chain_enrichment",
    "community_signal": "community_signal_enrichment",
    "context_efficiency": "context_efficiency_enrichment",
    "known_bad_pattern": "known_bad_pattern_enrichment",
    "tool_count": "tool_count_enrichment",
}


def _read_source(path: Path) -> str:
    """Read source file, return empty string on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _check_module_exists(module_name: str) -> bool:
    """Check if an enrichment module file exists on disk."""
    module_path = REPO_ROOT / f"{module_name}.py"
    return module_path.exists() and module_path.is_file()


def _extract_enricher_registry(source: str) -> dict[str, bool]:
    """
    Parse enrichment_dispatcher_daemon.py source to extract the enricher registry.
    Looks for get_enricher_registry() function and returns a dict of enricher_name -> enabled.
    """
    registry = {}

    # Pattern to match the get_enricher_registry function
    # This function returns a dict like: {"name": {"enabled": True, "module": "..."}}
    func_match = re.search(
        r'def get_enricher_registry\(\).*?:\s*"""(.*?)"""',
        source,
        re.DOTALL,
    )
    if not func_match:
        # Try without docstring
        func_match = re.search(
            r'def get_enricher_registry\(\).*?:\s*\n(.*?)(?=\ndef\s|\nif __name__|$)',
            source,
            re.DOTALL,
        )

    if not func_match:
        return registry

    func_body = func_match.group(1) if func_match.lastindex and func_match.group(1) else func_match.group(0)
    # Extract the return dict
    return_match = re.search(r'return\s*\{(.*?)\}', func_body, re.DOTALL)
    if not return_match:
        return registry

    return_body = return_match.group(1)

    # Parse each key: "name": {"enabled": True/False, ...}
    entry_pattern = re.compile(
        r'"(\w+)"\s*:\s*\{[^}]*"enabled"\s*:\s*(True|False)',
        re.DOTALL,
    )
    for match in entry_pattern.finditer(return_body):
        enricher_name = match.group(1)
        enabled = match.group(2) == "True"
        registry[enricher_name] = enabled

    return registry


def _extract_imports(source: str) -> list[str]:
    """Extract all import statements from source."""
    imports = []
    for line in source.splitlines():
        line = line.strip()
        if line.startswith("import ") or line.startswith("from "):
            imports.append(line)
    return imports


def _extract_dispatch_calls(source: str) -> list[str]:
    """
    Extract all compute_*_score function calls in dispatch_enrichment.
    These indicate which enrichers are actually dispatched.
    """
    calls = []
    # Find dispatch_enrichment function
    func_match = re.search(
        r'def dispatch_enrichment\(.*?\).*?(?=\ndef\s|\nclass\s|\nif __name__|$)',
        source,
        re.DOTALL,
    )
    if not func_match:
        return calls

    func_body = func_match.group(0)

    # Match compute_*_score(...) calls
    call_pattern = re.compile(r'compute_(\w+)_score\s*\(')
    for match in call_pattern.finditer(func_body):
        calls.append(match.group(1))

    return calls


def check_enrichment_dispatcher_coverage() -> dict[str, bool]:
    """
    Check which of the 5 required enrichment modules are wired in the dispatcher.

    A module is considered "wired" if:
      1. It appears in get_enricher_registry() with enabled=True, AND
      2. It has a compute_*_score call in dispatch_enrichment()

    Returns:
        dict with keys: 'supply_chain', 'community_signal', 'context_efficiency',
                        'known_bad_pattern', 'tool_count'
        Each value is True if wired, False otherwise.
    """
    source = _read_source(DISPATCHER_PATH)
    if not source:
        # File not found — all considered unwired
        return {key: False for key in REQUIRED_ENRICHMENTS}

    # Extract registry and dispatch calls
    registry = _extract_enricher_registry(source)
    dispatch_calls = _extract_dispatch_calls(source)

    # Check each required enrichment
    result = {}
    for short_key, module_name in REQUIRED_ENRICHMENTS.items():
        # Check if module exists
        module_exists = _check_module_exists(module_name)

        # Check if it's in the registry and enabled
        in_registry = registry.get(short_key, False)

        # Check if there's a dispatch call for it
        has_dispatch_call = short_key in dispatch_calls

        # Wired = exists + in registry + dispatch call
        wired = module_exists and in_registry and has_dispatch_call
        result[short_key] = wired

    return result


def report_missing_wirings() -> list[str]:
    """
    Return a list of enrichment names that are NOT wired.

    An enrichment is considered "not wired" if:
      - The module file exists, but
      - It's not in get_enricher_registry() OR has no dispatch call

    Returns:
        List of enrichment short-names that need wiring.
    """
    coverage = check_enrichment_dispatcher_coverage()
    return [key for key, wired in coverage.items() if not wired]


def print_coverage_report() -> None:
    """Print a human-readable coverage table to stdout."""
    coverage = check_enrichment_dispatcher_coverage()
    missing = report_missing_wirings()

    print("=" * 60)
    print("ENRICHMENT DISPATCHER COVERAGE REPORT")
    print("=" * 60)
    print(f"Dispatcher: {DISPATCHER_PATH}")
    print()

    # Table header
    print(f"{'Enrichment':<25} {'Module File':<30} {'Status':<10}")
    print("-" * 65)

    all_wired = True
    for short_key, module_name in REQUIRED_ENRICHMENTS.items():
        module_path = REPO_ROOT / f"{module_name}.py"
        module_exists = module_path.exists()
        wired = coverage[short_key]

        status = "WIRED" if wired else "MISSING"
        exists_str = "EXISTS" if module_exists else "ABSENT"

        print(f"{short_key:<25} {exists_str:<30} {status:<10}")

        if not wired:
            all_wired = False

    print("-" * 65)
    print()

    # Summary
    wired_count = sum(coverage.values())
    total_count = len(coverage)
    print(f"Coverage: {wired_count}/{total_count} enrichments wired")
    print()

    if missing:
        print(f"MISSING WIRINGS ({len(missing)}):")
        for key in missing:
            module_name = REQUIRED_ENRICHMENTS[key]
            print(f"  - {key} -> {module_name}")
        print()
        print("Suggested wiring tasks:")
        for key in missing:
            print(f"  - Wire {key} enricher into enrichment_dispatcher_daemon.py")
    else:
        print("All enrichments are wired!")

    print()
    print("=" * 60)


if __name__ == "__main__":
    # Run the coverage check
    result = check_enrichment_dispatcher_coverage()

    # Print the human-readable report
    print_coverage_report()

    # Assertions per acceptance criteria
    assert len(result) == 5, f"Expected 5 keys, got {len(result)}"
    assert all(key in result for key in REQUIRED_ENRICHMENTS), "Missing expected keys"

    wired_count = sum(result.values())

    if wired_count < 5:
        print("PASS: Coverage < 5/5 — gap identified for this cycle to target")
        sys.exit(0)
    else:
        print("FAIL: All enrichments wired — no gap to target")
        sys.exit(1)
