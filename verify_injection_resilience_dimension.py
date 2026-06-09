#!/usr/bin/env python3
"""Verify the injection_resilience wiring in trust_synthesiser_v2.py."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TARGET_FILE = ROOT / "trust_synthesiser_v2.py"
SCHEMA_FILE = ROOT / "DB_SCHEMA.md"
EXPECTED_DIMENSION = "injection_resilience"
EXPECTED_WEIGHT = 1.6
EXPECTED_THRESHOLD = 0.80
FLOAT_TOLERANCE = 1e-6


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_assignment_dict(source: str, name: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def find_sql_snippets(source: str, table_name: str) -> list[str]:
    pattern = re.compile(
        rf"(?is)SELECT.*?FROM\s+{re.escape(table_name)}.*?(?=(?:\n\s*def\s+|\n\s*@|\Z))"
    )
    return [match.group(0) for match in pattern.finditer(source)]


def extract_schema_section(schema_text: str, table_name: str) -> str:
    marker = f"## {table_name}"
    start = schema_text.find(marker)
    if start < 0:
        return ""
    remainder = schema_text[start:]
    next_marker = remainder.find("\n## ")
    return remainder if next_marker < 0 else remainder[:next_marker]


def schema_has_dimension_column(schema_text: str) -> bool:
    section = extract_schema_section(schema_text, "mcp_signal_scores")
    return bool(section and re.search(r"\|\s*dimension\s*\|", section, re.IGNORECASE))


def assert_self_test() -> None:
    sample_source = '''DEFAULT_SIGNAL_WEIGHTS = {"injection_resilience": 1.6, "scan_signal": 0.12}\nVERDICT_THRESHOLDS = {"TRUSTED": 0.80, "LIKELY_TRUSTED": 0.60}\n'''
    sample_weights = extract_assignment_dict(sample_source, "DEFAULT_SIGNAL_WEIGHTS")
    sample_thresholds = extract_assignment_dict(sample_source, "VERDICT_THRESHOLDS")
    assert sample_weights.get("injection_resilience") == 1.6
    assert sample_thresholds.get("TRUSTED") == 0.80
    assert find_sql_snippets("SELECT * FROM mcp_signal_scores WHERE dimension = 'injection_resilience'", "mcp_signal_scores")


def main() -> int:
    print("=" * 60)
    print("VERIFICATION: injection_resilience dimension wiring")
    print("=" * 60)

    assert_self_test()

    if not TARGET_FILE.is_file():
        print(f"[FAIL] Target file not found: {TARGET_FILE.name}")
        print("[DIAGNOSTIC STUB]")
        print("  - Missing target file; cannot verify wiring")
        return 1

    if not SCHEMA_FILE.is_file():
        print(f"[FAIL] Schema file not found: {SCHEMA_FILE.name}")
        print("[DIAGNOSTIC STUB]")
        print("  - Missing DB_SCHEMA.md; cannot verify table shape")
        return 1

    target_source = read_text(TARGET_FILE)
    schema_text = read_text(SCHEMA_FILE)

    weights = extract_assignment_dict(target_source, "DEFAULT_SIGNAL_WEIGHTS")
    thresholds = extract_assignment_dict(target_source, "VERDICT_THRESHOLDS")
    sql_snippets = find_sql_snippets(target_source, "mcp_signal_scores")
    dimension_in_sql = any(EXPECTED_DIMENSION in snippet.lower() for snippet in sql_snippets)
    query_mentions_dimension = any("dimension" in snippet.lower() for snippet in sql_snippets)
    schema_has_dimension = schema_has_dimension_column(schema_text)

    weight_value = weights.get(EXPECTED_DIMENSION)
    trusted_threshold = thresholds.get("TRUSTED")

    weight_ok = isinstance(weight_value, (int, float)) and abs(float(weight_value) - EXPECTED_WEIGHT) <= FLOAT_TOLERANCE
    threshold_ok = isinstance(trusted_threshold, (int, float)) and abs(float(trusted_threshold) - EXPECTED_THRESHOLD) <= FLOAT_TOLERANCE

    issues: list[str] = []
    if weight_value is None:
        issues.append(f"dimension '{EXPECTED_DIMENSION}' is missing from DEFAULT_SIGNAL_WEIGHTS")
    elif not weight_ok:
        issues.append(f"dimension '{EXPECTED_DIMENSION}' has weight {weight_value}, expected {EXPECTED_WEIGHT}")

    if trusted_threshold is None:
        issues.append("TRUSTED threshold is missing from VERDICT_THRESHOLDS")
    elif not threshold_ok:
        issues.append(f"TRUSTED threshold is {trusted_threshold}, expected {EXPECTED_THRESHOLD}")

    if not sql_snippets:
        issues.append("no mcp_signal_scores query was found")
    elif not query_mentions_dimension:
        issues.append("mcp_signal_scores query does not mention dimension filtering")
    if not dimension_in_sql:
        issues.append(f"mcp_signal_scores query does not filter on '{EXPECTED_DIMENSION}'")

    if not schema_has_dimension:
        issues.append("DB_SCHEMA.md shows no dimension column on mcp_signal_scores")

    print(f"[INFO] mcp_signal_scores queries found: {len(sql_snippets)}")
    print(f"[INFO] dimension column in schema: {'yes' if schema_has_dimension else 'no'}")
    print(f"[INFO] expected weight observed: {weight_value if weight_value is not None else 'missing'}")
    print(f"[INFO] TRUSTED threshold observed: {trusted_threshold if trusted_threshold is not None else 'missing'}")

    if issues:
        print()
        print("[DIAGNOSTIC STUB]")
        for issue in issues:
            print(f"  - {issue}")
        print()
        print("Expected wiring:")
        print(f"  - dimension: '{EXPECTED_DIMENSION}'")
        print(f"  - weight: {EXPECTED_WEIGHT}")
        print(f"  - threshold: {EXPECTED_THRESHOLD}")
        return 1

    print()
    print("[OK] wiring confirmed")
    print(f"  - dimension '{EXPECTED_DIMENSION}' weight = {EXPECTED_WEIGHT}")
    print(f"  - TRUSTED threshold = {EXPECTED_THRESHOLD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
