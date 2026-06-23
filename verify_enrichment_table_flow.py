#!/usr/bin/env python3
"""
verify_enrichment_table_flow.py

Diagnostic utility to verify all built enrichment modules are actually writing
rows to mcp_signal_enrichments.

PURPOSE: Audit the enrichment pipeline end-to-end. Identify which enrichment
modules (domain_trust_enrichment, supply_chain_enrichment, known_bad_pattern_enrichment,
tool_count_enrichment, temporal_stability_enrichment_v3, context_efficiency_enrichment)
are being called by signal_analyser and which are actually writing rows to
mcp_signal_enrichments. Report per-enrichment row counts and last-written timestamps.

INTERFACE: Single function verify_enrichment_pipeline_flow() -> dict with keys:
  - 'enrichment_row_counts': {signal_type: row_count}
  - 'enrichers_on_disk': [list of enrichment filenames found in enrichers/]
  - 'wired_in_signal_analyser': [list of import names signal_analyser.py imports]
  - 'unwired_enrichers': enrichers with files but no signal_analyser import
  - 'zero_row_enrichers': enrichers with rows=0
  - 'summary': one-line verdict

INPUTS: No user inputs; reads from DB via write_service /query on mcp_signal_enrichments
        and mcp_signal_scores.

OUTPUT: Prints a formatted audit report to stdout; returns the dict. Exit 0 on clean
        pipeline, exit 1 if any unwired or zero-row enrichers found.

CONSTRAINTS: Stdlib + requests only. No DB writes. Read-only diagnostic. No imports of
             protected modules.

ACCEPTANCE: Run `python3 verify_enrichment_table_flow.py`. Assert exit code 0 when all
            enrichers are wired and writing rows. Assert exit code 1 when gaps exist.
            Print PASS/FAIL with specific gap names.
"""

import ast
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

# deps: requests

# Expected enrichment module names (without .py extension)
EXPECTED_ENRICHERS = [
    "domain_trust_enrichment",
    "supply_chain_enrichment",
    "known_bad_pattern_enrichment",
    "tool_count_enrichment",
    "temporal_stability_enrichment_v3",
    "context_efficiency_enrichment",
]

# Mapping from enricher names to their signal_type values in the DB
ENRICHER_TO_SIGNAL_TYPE = {
    "domain_trust_enrichment": "domain_trust",
    "supply_chain_enrichment": "supply_chain",
    "known_bad_pattern_enrichment": "known_bad_pattern",
    "tool_count_enrichment": "tool_count",
    "temporal_stability_enrichment_v3": "temporal_stability",
    "context_efficiency_enrichment": "context_efficiency",
}

# Reverse mapping
SIGNAL_TYPE_TO_ENRICHER = {v: k for k, v in ENRICHER_TO_SIGNAL_TYPE.items()}

# Write service endpoint
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"


def find_project_root() -> Path:
    """Find the project root by looking for common markers."""
    current = Path(__file__).resolve().parent
    markers = ["pyproject.toml", "setup.py", ".git", "src", "tests", "enrichers"]

    while current != current.parent:
        for marker in markers:
            if (current / marker).exists():
                return current
        current = current.parent

    return Path(__file__).resolve().parent


def get_enrichers_directory() -> Path:
    """Get the path to the enrichers directory."""
    root = find_project_root()
    enrichers_dir = root / "enrichers"
    if enrichers_dir.exists() and enrichers_dir.is_dir():
        return enrichers_dir
    return root / "enrichers"


def get_signal_analyser_path() -> Optional[Path]:
    """Get the path to signal_analyser.py."""
    root = find_project_root()
    candidates = [
        root / "signal_analyser.py",
        root / "src" / "signal_analyser.py",
        root / "core" / "signal_analyser.py",
        root / "analyzers" / "signal_analyser.py",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    for candidate in root.rglob("signal_analyser.py"):
        if "/quarantine/" not in str(candidate):
            return candidate

    return None


def scan_enrichers_on_disk() -> List[str]:
    """Scan enrichers directory for Python files matching expected enricher names."""
    enrichers_dir = get_enrichers_directory()
    found = []

    if not enrichers_dir.exists():
        return found

    for py_file in enrichers_dir.glob("*.py"):
        name = py_file.stem
        if not name.startswith("_") and not name.startswith("test_"):
            found.append(name)

    return sorted(found)


def parse_signal_analyser_imports() -> List[str]:
    """Parse signal_analyser.py to find imported enrichment modules."""
    analyser_path = get_signal_analyser_path()

    if analyser_path is None:
        return []

    try:
        with open(analyser_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content)
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and ("enricher" in node.module.lower() or
                                   "enrichers" in node.module.lower() or
                                   node.module.startswith(".")):
                    for alias in node.names:
                        imports.append(alias.name)

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "enrichment" in alias.name.lower():
                        imports.append(alias.name)

        return sorted(set(imports))

    except Exception:
        return []


def scan_for_enrichment_calls_in_analyser() -> Set[str]:
    """Grep for enrichment module references in signal_analyser.py."""
    analyser_path = get_signal_analyser_path()

    if analyser_path is None:
        return set()

    found = set()

    try:
        with open(analyser_path, 'r', encoding='utf-8') as f:
            content = f.read()

        for enricher in EXPECTED_ENRICHERS:
            patterns = [
                f"import {enricher}",
                f"from {enricher}",
                f"from .{enricher}",
                f"from ..{enricher}",
                f"enrichers.{enricher}",
            ]
            for pattern in patterns:
                if pattern in content:
                    found.add(enricher)
                    break

        for enricher in EXPECTED_ENRICHERS:
            class_name = enricher.replace("_enrichment", "Enricher").title().replace("_", "")
            if class_name in content:
                found.add(enricher)

    except Exception:
        pass

    return found


def query_enrichment_row_counts() -> Dict[str, Tuple[int, Optional[str]]]:
    """
    Query mcp_signal_enrichments table for row counts per signal_type.
    Returns dict of {signal_type: (row_count, last_written_timestamp)}
    """
    result: Dict[str, Tuple[int, Optional[str]]] = {}

    try:
        response = requests.post(
            QUERY_ENDPOINT,
            json={
                "sql": """
                    SELECT signal_type, COUNT(*) as cnt, MAX(created_at) as last_ts
                    FROM mcp_signal_enrichments
                    GROUP BY signal_type
                """,
                "params": []
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            rows = data.get("rows", []) if isinstance(data, dict) else []
            for row in rows:
                if isinstance(row, dict):
                    signal_type = row.get("signal_type", "")
                    count = row.get("cnt", 0)
                    last_ts = row.get("last_ts")
                    result[signal_type] = (count, last_ts)
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    result[row[0]] = (row[1], row[2] if len(row) > 2 else None)

    except Exception as e:
        print(f"[WARN] Could not query mcp_signal_enrichments: {e}")

    return result


def query_signal_scores_row_count() -> int:
    """Query total row count in mcp_signal_scores table."""
    try:
        response = requests.post(
            QUERY_ENDPOINT,
            json={
                "sql": "SELECT COUNT(*) as cnt FROM mcp_signal_scores",
                "params": []
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            rows = data.get("rows", []) if isinstance(data, dict) else []
            if rows and isinstance(rows[0], dict):
                return rows[0].get("cnt", 0)
            elif rows and isinstance(rows[0], (list, tuple)):
                return rows[0][0]

    except Exception as e:
        print(f"[WARN] Could not query mcp_signal_scores: {e}")

    return 0


def verify_enrichment_pipeline_flow() -> dict:
    """
    Main verification function.

    Returns dict with:
      - enrichment_row_counts: {signal_type: row_count}
      - enrichers_on_disk: [list of enrichment filenames]
      - wired_in_signal_analyser: [list of import names]
      - unwired_enrichers: enrichers with files but no signal_analyser import
      - zero_row_enrichers: enrichers with rows=0
      - summary: one-line verdict
    """
    print("=" * 70)
    print("ENRICHMENT PIPELINE FLOW VERIFICATION")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Project Root: {find_project_root()}")
    print()

    enrichers_on_disk = scan_enrichers_on_disk()
    print(f"[1] ENRICHERS ON DISK ({len(enrichers_on_disk)} found):")
    for e in enrichers_on_disk:
        marker = "✓" if e in EXPECTED_ENRICHERS else "?"
        print(f"    {marker} {e}")
    print()

    wired_imports = parse_signal_analyser_imports()
    wired_calls = scan_for_enrichment_calls_in_analyser()
    wired_in_analyser = sorted(set(wired_imports + list(wired_calls)))

    print(f"[2] WIRED IN signal_analyser.py ({len(wired_in_analyser)} found):")
    for w in wired_in_analyser:
        print(f"    -> {w}")
    if not wired_in_analyser:
        print("    [NONE FOUND]")
    print()

    db_stats = query_enrichment_row_counts()
    enrichment_row_counts = {k: v[0] for k, v in db_stats.items()}

    print(f"[3] ENRICHMENT ROW COUNTS (from mcp_signal_enrichments):")
    if db_stats:
        for signal_type, (count, last_ts) in sorted(db_stats.items()):
            last_str = last_ts[:19] if last_ts and isinstance(last_ts, str) else str(last_ts) if last_ts else "NEVER"
            print(f"    {signal_type}: {count:,} rows (last: {last_str})")
    else:
        print("    [NO DATA - verify write_service is running]")
    print()

    signal_scores_count = query_signal_scores_row_count()
    print(f"[3b] MCP_SIGNAL_SCORES total rows: {signal_scores_count:,}")
    print()

    expected_found_on_disk = [e for e in EXPECTED_ENRICHERS if e in enrichers_on_disk]
    expected_wired = [e for e in EXPECTED_ENRICHERS if e in wired_in_analyser]

    unwired_enrichers = [e for e in expected_found_on_disk if e not in wired_in_analyser]
    zero_row_enrichers = []

    for enricher in expected_wired:
        signal_type = ENRICHER_TO_SIGNAL_TYPE.get(enricher)
        if signal_type:
            count, _ = db_stats.get(signal_type, (0, None))
            if count == 0:
                zero_row_enrichers.append(enricher)

    print(f"[4] GAP ANALYSIS:")

    if unwired_enrichers:
        print(f"    UNWIRED ENRICHERS (files exist, not imported):")
        for e in unwired_enrichers:
            print(f"      ! {e}")
    else:
        print(f"    [OK] All enrichers with files are wired")

    if zero_row_enrichers:
        print(f"    ZERO-ROW ENRICHERS (wired but no data):")
        for e in zero_row_enrichers:
            print(f"      ! {e}")
    else:
        print(f"    [OK] All wired enrichers are writing rows")

    missing_files = [e for e in EXPECTED_ENRICHERS if e not in enrichers_on_disk]
    if missing_files:
        print(f"    MISSING ENRICHMENT FILES:")
        for e in missing_files:
            print(f"      X {e}.py")
    print()

    has_gaps = bool(unwired_enrichers or zero_row_enrichers or missing_files)

    if not has_gaps:
        summary = "PASS - All enrichment modules are wired and writing rows"
        status = "PASS"
    else:
        gap_names = []
        gap_names.extend([f"{e} (unwired)" for e in unwired_enrichers])
        gap_names.extend([f"{e} (zero rows)" for e in zero_row_enrichers])
        gap_names.extend([f"{e} (missing file)" for e in missing_files])
        summary = f"FAIL - Gaps found: {', '.join(gap_names)}"
        status = "FAIL"

    print("=" * 70)
    print(f"SUMMARY: {summary}")
    print("=" * 70)

    return {
        'enrichment_row_counts': enrichment_row_counts,
        'enrichers_on_disk': enrichers_on_disk,
        'wired_in_signal_analyser': wired_in_analyser,
        'unwired_enrichers': unwired_enrichers,
        'zero_row_enrichers': zero_row_enrichers,
        'summary': summary,
        'status': status,
    }


def main() -> int:
    """Entry point."""
    try:
        result = verify_enrichment_pipeline_flow()

        print()
        print("RETURN DICT:")
        print(f"  enrichment_row_counts: {result['enrichment_row_counts']}")
        print(f"  enrichers_on_disk: {result['enrichers_on_disk']}")
        print(f"  wired_in_signal_analyser: {result['wired_in_signal_analyser']}")
        print(f"  unwired_enrichers: {result['unwired_enrichers']}")
        print(f"  zero_row_enrichers: {result['zero_row_enrichers']}")
        print(f"  summary: {result['summary']}")

        if result['status'] == 'PASS':
            print()
            print("PASS - PIPELINE CLEAN - Exit 0")
            return 0
        else:
            print()
            print("FAIL - GAPS DETECTED - Exit 1")
            return 1

    except Exception as e:
        print(f"[ERROR] Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())