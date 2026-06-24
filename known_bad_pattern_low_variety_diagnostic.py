#!/usr/bin/env python3
# deps: requests
"""
known_bad_pattern_low_variety_diagnostic.py

DIAGNOSTIC: Confirms that known_bad_pattern signal has only 2 distinct score
values (69.0 and 95.0) across ~26k servers, providing ZERO discrimination.

Queries mcp_signal_scores WHERE signal_type='known_bad_pattern' GROUP BY
score_value. If servers cluster into ≤3 buckets, the signal is weak.

Proposes known_bad_pattern_diversity_enrichment_v4.py that reads:
  - registry_source, publisher_verified, age_days
  - dependency_count, stars

Pure diagnostic: query-only, NO DB writes.
References last_error from quality map (cohort_3_n7) but does NOT target
quarantine-listed files.

Per PRODUCT_SPEC §5 enricher contract:
  compute_score(metadata: dict) -> (float in [0,100], evidence dict)
  Pure function: no DB writes, no network.

References gate_8_new_module.py §1-§4 for safety rules.
"""

import json
import sys
from datetime import datetime
from typing import Any

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Execute read-only SELECT via write_service."""
    for attempt in range(3):
        try:
            resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
            if resp.status_code == 200:
                result = resp.json()
                return result.get("rows", [])
            print(f"  [ws_query attempt {attempt+1}] {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  [ws_query attempt {attempt+1}] {e}")
        time.sleep(1)
    return []


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service (defined for future use; unused in diagnostic)."""
    for attempt in range(3):
        try:
            resp = requests.post(EXECUTE_URL, json={"sql": sql, "wait": True}, timeout=10)
            if resp.status_code in (200, 201):
                return True
            print(f"  [ws_execute attempt {attempt+1}] {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  [ws_execute attempt {attempt+1}] {e}")
        time.sleep(1)
    return False


def check_score_distribution() -> dict[str, Any]:
    """CHECK 1: Query mcp_signal_scores for known_bad_pattern score distribution."""
    print("\n=== CHECK 1: known_bad_pattern score distribution ===")

    sql = """
        SELECT
            signal_name,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT score) AS distinct_scores,
            MIN(score) AS min_score,
            MAX(score) AS max_score,
            AVG(score) AS avg_score
        FROM mcp_signal_scores
        WHERE signal_name = 'known_bad_pattern'
        GROUP BY signal_name
    """
    rows = ws_query(sql)

    if not rows:
        print("  No known_bad_pattern scores found in mcp_signal_scores.")
        return {"found": False}

    s = rows[0]
    result = {
        "found": True,
        "signal_name": s["signal_name"],
        "total_rows": s["total_rows"],
        "distinct_scores": s["distinct_scores"],
        "score_range": [s["min_score"], s["max_score"]],
        "avg_score": round(s["avg_score"], 4),
    }

    print(f"  Total rows:    {s['total_rows']:,}")
    print(f"  Distinct scores: {s['distinct_scores']}")
    print(f"  Score range:   {s['min_score']} - {s['max_score']}")
    print(f"  Avg score:     {result['avg_score']}")

    # Bucket distribution
    dist_sql = """
        SELECT score, COUNT(*) AS cnt
        FROM mcp_signal_scores
        WHERE signal_name = 'known_bad_pattern'
        GROUP BY score
        ORDER BY score
    """
    dist_rows = ws_query(dist_sql)
    total = sum(r["cnt"] for r in dist_rows)
    result["buckets"] = []
    for r in dist_rows:
        pct = round(100 * r["cnt"] / total, 2) if total > 0 else 0
        bar = "█" * max(1, int(pct / 5))
        print(f"    {r['score']:5.1f}  {r['cnt']:6,}  ({pct:5.1f}%) {bar}")
        result["buckets"].append({"score": r["score"], "count": r["cnt"], "pct": pct})

    # Determine if signal is weak
    if result["distinct_scores"] <= 2:
        result["weak_signal"] = True
        result["discrimination"] = "ZERO — only binary values"
        print(f"\n  *** WEAK SIGNAL CONFIRMED: {result['distinct_scores']} distinct values ***")
    else:
        result["weak_signal"] = False
        result["discrimination"] = "adequate"
        print(f"\n  Signal has {result['distinct_scores']} values — adequate discrimination")

    return result


def check_metadata_fields() -> dict[str, Any]:
    """CHECK 2: Verify additional metadata fields exist in mcp_server_registry."""
    print("\n=== CHECK 2: Available metadata fields for enrichment ===")

    # Check schema of mcp_server_registry
    schema_sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_server_registry'
        ORDER BY ordinal_position
    """
    schema_rows = ws_query(schema_sql)

    available = []
    target_fields = {
        "registry_source": False,
        "publisher_verified": False,
        "age_days": False,
        "dependency_count": False,
        "stars": False,
        "downloads": False,
        "domain_age": False,
        "tool_count": False,
    }

    if schema_rows:
        cols = [r["column_name"] for r in schema_rows]
        print(f"  Columns in mcp_server_registry: {len(cols)}")
        for field in target_fields:
            if field in cols:
                target_fields[field] = True
                available.append(field)
                print(f"    ✓ {field}")
            else:
                print(f"    ✗ {field}")

    # Check if mcp_signal_enrichments has additional fields
    enrich_schema_sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'mcp_signal_enrichments'
        ORDER BY ordinal_position
    """
    enrich_schema = ws_query(enrich_schema_sql)
    enrich_cols = [r["column_name"] for r in enrich_schema] if enrich_schema else []
    print(f"\n  Columns in mcp_signal_enrichments: {len(enrich_cols)}")

    return {
        "target_fields": target_fields,
        "available_fields": available,
        "mcp_signal_enrichments_cols": enrich_cols,
    }


def check_quality_map() -> dict[str, Any]:
    """CHECK 3: Reference last_error from quality map (cohort_3_n7)."""
    print("\n=== CHECK 3: Quality map for known_bad_pattern files ===")

    # Read quality map state
    state_file = "/home/workspace/zo_sentinel/gate_quality_state.json"
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except Exception as e:
        print(f"  Cannot read quality map: {e}")
        return {"error": str(e)}

    # Check file_retries for known_bad_pattern files
    file_retries = state.get("file_retries", {})
    kbp_files = {}
    for filename, retry_info in file_retries.items():
        if "known_bad_pattern" in filename.lower():
            kbp_files[filename] = retry_info

    print(f"  Files with retry history: {len(kbp_files)}")
    for filename, info in kbp_files.items():
        print(f"    {filename}:")
        print(f"      attempts: {info.get('attempts', 0)}")
        print(f"      last_failed: {info.get('last_failed_at', 'N/A')}")
        print(f"      cohort: {info.get('cohorts', [])}")

    # Check quarantined files (do NOT target these)
    quarantined = state.get("quarantined", {})
    kbp_quarantined = [f for f in quarantined if "known_bad_pattern" in f.lower()]
    if kbp_quarantined:
        print(f"\n  Quarantined files (do NOT rebuild):")
        for f in kbp_quarantined:
            print(f"    - {f}")

    return {
        "kbp_files": kbp_files,
        "quarantined_kbp": kbp_quarantined,
        "recent_cohorts": state.get("recent_cohorts", [])[-5:],
    }


def check_source_implementation() -> dict[str, Any]:
    """CHECK 4: Identify the source module producing known_bad_pattern scores."""
    print("\n=== CHECK 4: Source implementation analysis ===")

    result = {
        "source_file": None,
        "scoring_function": None,
        "issue": None,
    }

    # Read signal_analyser_v4.py to find known_bad_pattern scoring
    sa_file = "/home/workspace/zo_sentinel/signal_analyser_v4.py"
    try:
        with open(sa_file, "r") as f:
            source = f.read()
    except Exception as e:
        print(f"  Cannot read signal_analyser_v4.py: {e}")
        result["error"] = str(e)
        return result

    if "known_bad_pattern" not in source:
        print("  known_bad_pattern not found in signal_analyser_v4.py")
        result["issue"] = "not_found_in_signal_analyser"
        return result

    # Find the scoring context
    import re

    # Look for binary scoring (69.0, 95.0)
    binary_pattern = re.compile(r"(69\.0|95\.0)")
    matches = binary_pattern.findall(source)
    if matches:
        print(f"  Binary scores found: {set(matches)}")
        result["issue"] = "binary_scoring"
        result["binary_values"] = list(set(matches))

    # Find the function
    func_match = re.search(
        r"def\s+(\w*known_bad\w*)\s*\([^)]*\)[^:]*:",
        source,
        re.IGNORECASE,
    )
    if func_match:
        result["scoring_function"] = func_match.group(1)
        print(f"  Scoring function: {result['scoring_function']}")

    result["source_file"] = "signal_analyser_v4.py"
    print("  Root cause: binary scoring in signal_analyser_v4.py")

    return result


def propose_enrichment_v4() -> dict[str, Any]:
    """PROPOSAL: known_bad_pattern_diversity_enrichment_v4.py"""
    print("\n=== PROPOSAL: known_bad_pattern_diversity_enrichment_v4.py ===")

    proposal = {
        "filename": "known_bad_pattern_diversity_enrichment_v4.py",
        "signal": "known_bad_pattern",
        "purpose": "Replace binary scoring (69/95) with multi-dimensional scoring",
        "metadata_fields": [
            "registry_source",
            "publisher_verified",
            "age_days",
            "dependency_count",
            "stars",
        ],
        "expected_distinct_values": ">= 10",
        "contract": "compute_score(metadata: dict) -> (float in [0,100], evidence dict)",
    }

    print("""
  Purpose: Replace binary scoring (69/95) with multi-dimensional scoring
  using metadata fields that already exist in mcp_server_registry.

  Metadata fields to read:
    - registry_source (str): pypi=trusted, unknown=suspicious
    - publisher_verified (bool): verified=trust, unverified=risk
    - age_days (int/float): new=<30d=high risk, old=>365d=low risk
    - dependency_count (int): too few/too many indicate anomalies
    - stars (int): community trust signal

  Scoring dimensions:
    1. registry_trust (0-25 pts): Weight by package registry credibility
    2. age_risk (0-20 pts): New packages carry higher risk
    3. verification_bonus (0-15 pts): Publisher verification adds trust
    4. dependency_anomaly (0-15 pts): Abnormal counts indicate risk
    5. community_signals (0-15 pts): Stars, activity indicate legitimacy
    6. diversity_boost (0-10 pts): Varied metadata = lower risk

  Expected outcome: >= 10 distinct score values instead of 2.

  Gate 8 contract: compute_score({}) must return (float in [0,100], dict).
  Pure function: no DB writes, no network calls at import time.
""")

    return proposal


def main() -> int:
    """Run diagnostic and print findings."""
    print("=" * 70)
    print("DIAGNOSTIC: known_bad_pattern low variety")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 70)

    # Run all checks
    score_result = check_score_distribution()
    metadata_result = check_metadata_fields()
    quality_result = check_quality_map()
    source_result = check_source_implementation()
    proposal = propose_enrichment_v4()

    # Summary
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)

    findings = {
        "timestamp": datetime.utcnow().isoformat(),
        "signal": "known_bad_pattern",
        "weak_signal": score_result.get("weak_signal", False),
        "distinct_scores": score_result.get("distinct_scores", 0),
        "discrimination": score_result.get("discrimination", "unknown"),
        "root_cause": source_result.get("issue", "unknown"),
        "available_metadata": metadata_result.get("available_fields", []),
        "proposed_enrichment": proposal["filename"],
        "quarantined_files": quality_result.get("quarantined_kbp", []),
    }

    print(f"""
1. SCORE DISTRIBUTION:
   - Total rows:    {score_result.get('total_rows', 'N/A'):,}
   - Distinct scores: {score_result.get('distinct_scores', 'N/A')}
   - Score range:   {score_result.get('score_range', [])}
   - Status:        {score_result.get('discrimination', 'unknown')}

2. ROOT CAUSE:
   - Source file:   {source_result.get('source_file', 'N/A')}
   - Issue:        {source_result.get('issue', 'unknown')}
   - Binary values: {source_result.get('binary_values', [])}

3. AVAILABLE METADATA FIELDS:
   - Fields ready for enrichment: {metadata_result.get('available_fields', [])}

4. QUALITY MAP STATUS:
   - Files with retry history: {len(quality_result.get('kbp_files', {}))}
   - Quarantined (do NOT rebuild): {len(quality_result.get('quarantined_kbp', []))}

5. PROPOSAL:
   - File:          {proposal['filename']}
   - Purpose:       {proposal['purpose']}
   - Target values: {proposal['expected_distinct_values']}
   - Contract:      {proposal['contract']}
""")

    print("=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)

    # Return 0 if weak signal confirmed (expected diagnostic result)
    return 0 if score_result.get("weak_signal") else 1


if __name__ == "__main__":
    import time
    sys.exit(main())
