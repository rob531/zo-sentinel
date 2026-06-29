#!/usr/bin/env python3
"""
verify_enrichment_wiring_for_recent_builds.py

Diagnostic utility to verify that recently built enrichment modules are properly
wired into mcp_signal_enrichments. Queries the table for distinct signal_types
and count rows per enrichment, then reports which enrichments have 0 rows (not
wired) vs those with rows.

Prints PASS if all built enrichments have evidence rows, FAIL with specific
missing modules otherwise.

Pure diagnostic -- NO DB writes, no network except write_service HTTP.
"""

# deps: requests

import sys
from datetime import datetime

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"

# Recently built enrichment modules to verify
# Maps module name -> expected signal_type in mcp_signal_enrichments
BUILT_ENRICHMENTS = {
    "known_bad_pattern_enrichment_v2": "known_bad_pattern",
    "tool_count_enrichment_v2": "tool_count",
    "tool_description_safety_enrichment": "tool_description_safety",
    "temporal_stability_enrichment_v2": "temporal_stability",
    "permission_scope_enrichment_v2": "permission_scope",
}


def query_service(sql: str, params: list | None = None) -> dict:
    """Execute SELECT query via write_service HTTP API."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        response = requests.post(
            QUERY_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Could not connect to write_service at {WRITE_SERVICE_URL}")
        print(f"Detail: {e}")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP error from write_service: {e}")
        print(f"Response: {e.response.text if e.response else 'N/A'}")
        sys.exit(1)


def get_enrichment_counts() -> dict[str, int]:
    """Query mcp_signal_enrichments for row counts per signal_type."""
    sql = """
        SELECT 
            signal_type,
            COUNT(*) as row_count
        FROM mcp_signal_enrichments
        GROUP BY signal_type
        ORDER BY signal_type
    """
    result = query_service(sql)
    rows = result.get("rows", [])
    return {row["signal_type"]: row["row_count"] for row in rows}


def get_total_row_count() -> int:
    """Get total row count in mcp_signal_enrichments."""
    sql = "SELECT COUNT(*) as total FROM mcp_signal_enrichments"
    result = query_service(sql)
    rows = result.get("rows", [])
    if rows:
        return rows[0].get("total", 0)
    return 0


def main() -> int:
    print("=" * 70)
    print("ENRICHMENT WIRING VERIFICATION FOR RECENT BUILDS")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Write Service: {WRITE_SERVICE_URL}")
    print()
    print("Checking built enrichments:")
    for module, signal_type in BUILT_ENRICHMENTS.items():
        print(f"  {module} -> signal_type={signal_type}")
    print("=" * 70)

    # Query current enrichment counts
    enrichment_counts = get_enrichment_counts()
    total_rows = get_total_row_count()

    print(f"\nTotal rows in mcp_signal_enrichments: {total_rows}")
    print(f"Distinct signal_types found: {len(enrichment_counts)}")

    if enrichment_counts:
        print("\nSignal types with rows:")
        for signal_type, count in sorted(enrichment_counts.items()):
            print(f"  {signal_type}: {count} rows")
    else:
        print("\nWARNING: No enrichments found in mcp_signal_enrichments!")

    # Check each built enrichment
    print("\n" + "=" * 70)
    print("WIRING STATUS PER BUILT ENRICHMENT")
    print("=" * 70)

    missing_enrichments = []
    wired_enrichments = []

    for module, signal_type in BUILT_ENRICHMENTS.items():
        row_count = enrichment_counts.get(signal_type, 0)
        if row_count > 0:
            status = f"WIRED ({row_count} rows)"
            wired_enrichments.append((module, signal_type, row_count))
        else:
            status = "NOT WIRED (0 rows)"
            missing_enrichments.append((module, signal_type))

        print(f"\n  {module}:")
        print(f"    signal_type: {signal_type}")
        print(f"    status: {status}")

    # Final verdict
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)

    if not missing_enrichments:
        print("PASS: All built enrichments have evidence rows in mcp_signal_enrichments.")
        print(f"\nWired enrichments ({len(wired_enrichments)}):")
        for module, signal_type, count in wired_enrichments:
            print(f"  - {module}: {count} rows")
        return 0
    else:
        print(f"FAIL: {len(missing_enrichments)} enrichment(s) not wired to mcp_signal_enrichments:")
        for module, signal_type in missing_enrichments:
            print(f"  - {module} (signal_type={signal_type})")
        print()
        print("Action required: Wire these enrichments to write to mcp_signal_enrichments")
        print("or ensure the enrichment daemon/harness is processing them.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
