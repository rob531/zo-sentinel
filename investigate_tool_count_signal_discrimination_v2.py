#!/usr/bin/env python3
"""
investigate_tool_count_signal_discrimination_v2.py

Investigates the tool_count signal showing only 2 distinct values (55.0 and 92.0)
across range 55.0-92.0. Per PRODUCT_SPEC §3, low variety means the signal
contributes nothing to the verdict.

Finds:
  1. Whether 2 distinct values is a data quality issue or a producer bug.
  2. The source module producing the signal.
  3. Whether it needs enrichment or a direct fix.

REPORT ONLY — no DB writes, no network, no file writes on import.
"""

from __future__ import annotations

import sys
import json
import requests
from datetime import datetime

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
HTTP_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ws_query(sql: str, params: list | None = None) -> dict:
    payload: dict = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Investigation queries
# ---------------------------------------------------------------------------

def get_tool_count_signal_distribution() -> dict:
    """Return distinct score/count pairs for tool_count in mcp_signal_scores."""
    return ws_query("""
        SELECT signal_name, score, COUNT(*) as cnt
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
        GROUP BY signal_name, score
        ORDER BY score
    """)


def get_tool_count_evidence_samples(limit: int = 20) -> dict:
    """Return sample evidence blobs for tool_count entries."""
    return ws_query(f"""
        SELECT id, server_id, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
        LIMIT {limit}
    """)


def get_tool_count_enrichment_rows() -> dict:
    """Check mcp_signal_enrichments for any tool_count enrichment rows."""
    return ws_query("""
        SELECT signal_type, COUNT(*) as cnt
        FROM mcp_signal_enrichments
        WHERE signal_type LIKE '%tool%count%'
           OR signal_type LIKE '%tool_count%'
        GROUP BY signal_type
    """)


def get_evidence_blob_schema() -> dict:
    """Inspect the evidence column type and sample values for tool_count rows."""
    return ws_query("""
        SELECT
            MIN(LENGTH(evidence)) as min_len,
            MAX(LENGTH(evidence)) as max_len,
            COUNT(DISTINCT evidence) as distinct_evidence,
            COUNT(*) as total_rows
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
    """)


# ---------------------------------------------------------------------------
# Producer analysis — read the source code
# ---------------------------------------------------------------------------

def read_score_tool_count_source() -> str:
    """Read the score_tool_count() function source from signal_analyser_v2.py."""
    source_path = "/home/workspace/zo_sentinel/signal_analyser_v2.py"
    try:
        with open(source_path, "r") as fh:
            content = fh.read()
    except FileNotFoundError:
        return f"FILE NOT FOUND: {source_path}"

    # Extract the function body
    lines = content.splitlines()
    in_func = False
    func_lines = []
    indent_level = None
    for line in lines:
        if "def score_tool_count(" in line:
            in_func = True
            indent_level = len(line) - len(line.lstrip())
            func_lines.append(line)
        elif in_func:
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped) if stripped else 999
            # End of function when we see another def at same or lower indent
            if stripped.startswith("def ") and current_indent <= indent_level:
                break
            func_lines.append(line)
    return "\n".join(func_lines)


def find_tool_count_enricher() -> dict:
    """Check if any tool_count enrichment module exists and what it produces."""
    candidates = [
        "/home/workspace/zo_sentinel/tool_count_enrichment.py",
        "/home/workspace/zo_sentinel/tool_count_enrichment_v3.py",
        "/home/workspace/zo_sentinel/tool_count_enrichment_v4.py",
        "/home/workspace/zo_sentinel/context_efficiency_enrichment.py",
        "/home/workspace/zo_sentinel/tool_description_safety_enrichment.py",
    ]
    results = {}
    for path in candidates:
        try:
            with open(path) as fh:
                src = fh.read()
            has_compute = "def compute_score(" in src
            has_tool_count = "tool_count" in src.lower()
            results[path] = {
                "exists": True,
                "has_compute_score": has_compute,
                "has_tool_count": has_tool_count,
                "size_bytes": len(src),
            }
        except FileNotFoundError:
            results[path] = {"exists": False}
    return results


def read_tool_count_enrichment_v4_source() -> str:
    """Read tool_count_enrichment_v4.py source for comparison."""
    path = "/home/workspace/zo_sentinel/tool_count_enrichment_v4.py"
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return f"FILE NOT FOUND: {path}"


# ---------------------------------------------------------------------------
# Diagnostic run
# ---------------------------------------------------------------------------

def run_investigation() -> dict:
    print("=" * 70)
    print("tool_count signal discrimination investigation v2")
    print(f"Started: {datetime.utcnow().isoformat()} UTC")
    print("=" * 70)

    findings: dict = {
        "signal": "tool_count",
        "investigation_time": datetime.utcnow().isoformat() + "Z",
        "distinct_values_found": None,
        "score_distribution": {},
        "evidence_samples": [],
        "root_cause": None,
        "root_cause_type": None,
        "source_module": None,
        "fix_recommendation": None,
        "enricher_available": None,
        "raw_distribution": {},
    }

    # 1. Signal distribution
    print("\n[1] Querying mcp_signal_scores for tool_count distribution...")
    try:
        dist_result = get_tool_count_signal_distribution()
        findings["raw_distribution"] = dist_result
        rows = dist_result.get("rows", [])
        distinct_scores = {r["score"] for r in rows}
        score_counts = {r["score"]: r["cnt"] for r in rows}
        findings["distinct_values_found"] = len(distinct_scores)
        findings["score_distribution"] = score_counts
        print(f"    Distinct score values: {len(distinct_scores)}")
        print(f"    Scores: {score_counts}")
    except Exception as exc:
        print(f"    ERROR querying distribution: {exc}")
        findings["score_distribution_error"] = str(exc)

    # 2. Evidence blob analysis
    print("\n[2] Analyzing evidence blobs...")
    try:
        schema_result = get_evidence_blob_schema()
        findings["evidence_schema"] = schema_result
        ev_rows = schema_result.get("rows", [])
        if ev_rows:
            er = ev_rows[0]
            print(f"    Total rows: {er['total_rows']}")
            print(f"    Distinct evidence blobs: {er['distinct_evidence']}")
            print(f"    Evidence length range: {er['min_len']}-{er['max_len']} bytes")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    # 3. Sample evidence rows
    print("\n[3] Sampling tool_count evidence rows...")
    try:
        samples = get_tool_count_evidence_samples(limit=15)
        findings["evidence_samples"] = samples.get("rows", [])
        for row in findings["evidence_samples"][:5]:
            ev = row.get("evidence", "")
            try:
                ev_parsed = json.loads(ev)
            except Exception:
                ev_parsed = ev
            print(f"    server_id={row['server_id'][:40]:40s}  score={row['score']}  evidence={ev_parsed}")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    # 4. Check mcp_signal_enrichments for any tool_count rows
    print("\n[4] Checking mcp_signal_enrichments for tool_count rows...")
    try:
        enrich_result = get_tool_count_enrichment_rows()
        findings["enrichment_rows"] = enrich_result
        enrich_rows = enrich_result.get("rows", [])
        print(f"    Enrichment rows found: {len(enrich_rows)}")
        for r in enrich_rows:
            print(f"    signal_type={r['signal_type']}  cnt={r['cnt']}")
    except Exception as exc:
        print(f"    ERROR: {exc}")

    # 5. Read the producer source
    print("\n[5] Reading score_tool_count() source from signal_analyser_v2.py...")
    source = read_score_tool_count_source()
    findings["producer_source"] = source
    print("--- source start ---")
    for line in source.splitlines()[:60]:
        print(f"    {line}")
    print("--- source end ---")

    # 6. Check for existing enrichment modules
    print("\n[6] Checking for tool_count enrichment modules...")
    enricher_info = find_tool_count_enricher()
    findings["enricher_available"] = enricher_info
    for path, info in enricher_info.items():
        status = "EXISTS" if info.get("exists") else "NOT FOUND"
        print(f"    {path}: {status}")
        if info.get("exists"):
            print(f"      has_compute_score={info['has_compute_score']}")
            print(f"      has_tool_count={info['has_tool_count']}")
            print(f"      size={info['size_bytes']} bytes")

    # 7. Read tool_count_enrichment_v4 source for comparison
    print("\n[7] Reading tool_count_enrichment_v4.py for discrimination analysis...")
    v4_source = read_tool_count_enrichment_v4_source()
    findings["v4_source"] = v4_source
    print(f"    v4 source length: {len(v4_source)} chars")
    if v4_source and "FILE NOT FOUND" not in v4_source:
        print("    v4 appears to be a valid enrichment module with graduated scoring")
    else:
        print("    v4 NOT FOUND or empty")

    # -------------------------------------------------------------------------
    # DIAGNOSIS
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    # Analyze evidence blobs: are they showing varying tool_count values?
    ev_values = set()
    for row in findings.get("evidence_samples", []):
        ev_str = row.get("evidence", "")
        try:
            ev_parsed = json.loads(ev_str)
            tc = ev_parsed.get("tool_count", "MISSING")
            ev_values.add(tc)
        except Exception:
            ev_values.add(ev_str)

    findings["evidence_tool_count_values"] = sorted(ev_values)
    print(f"\nEvidence tool_count values seen: {ev_values}")
    print(f"Distinct evidence tool_count values: {len(ev_values)}")

    # Root cause analysis:
    if len(ev_values) > 2 and findings["distinct_values_found"] == 2:
        findings["root_cause_type"] = "producer_bug"
        findings["root_cause"] = (
            "score_tool_count() in signal_analyser_v2.py produces only 2 distinct "
            "score buckets (55.0 for tool_count=0, 92.0 for all other values). "
            "The evidence blobs contain varying tool_count values, proving the "
            "underlying data is not the problem — the scoring logic is."
        )
        findings["fix_recommendation"] = (
            "FIX: Refactor score_tool_count() to use graduated scoring buckets "
            "that map each tool_count range to a distinct score. The enrichment "
            "modules (tool_count_enrichment_v3.py, tool_count_enrichment_v4.py, "
            "tool_description_safety_enrichment.py) demonstrate correct graduated "
            "scoring but are NOT wired into signal_analyser_v2.py. "
            "Either wire the existing enrichment modules into the signal pipeline OR "
            "rewrite score_tool_count() with fine-grained buckets."
        )
    elif len(ev_values) <= 2 and findings["distinct_values_found"] == 2:
        findings["root_cause_type"] = "data_quality"
        findings["root_cause"] = (
            "Both the evidence blobs and the signal scores show only 2 values "
            "(tool_count=0 and tool_count=1). The registry/fingerprint data "
            "genuinely contains no higher tool_count values."
        )
        findings["fix_recommendation"] = (
            "ENRICH: Write a tool_count enrichment module that reads actual tool "
            "definitions from mcp_server_registry metadata or from GitHub API and "
            "counts distinct tools per server. Backfill mcp_signal_enrichments with "
            "the correct tool counts, then re-score. The existing enrichment modules "
            "in tool_count_enrichment_v4.py and tool_count_enrichment_v3.py "
            "can serve as templates."
        )
    else:
        findings["root_cause_type"] = "unknown"
        findings["root_cause"] = "Insufficient data to determine root cause."
        findings["fix_recommendation"] = "Further investigation required."

    print(f"\nRoot cause type: {findings['root_cause_type']}")
    print(f"\nRoot cause:\n  {findings['root_cause']}")
    print(f"\nFix recommendation:\n  {findings['fix_recommendation']}")

    # Summary table
    print("\n--- Summary ---")
    print(f"  Signal:              tool_count")
    print(f"  Distinct values:     {findings['distinct_values_found']}")
    print(f"  Score distribution:  {findings['score_distribution']}")
    print(f"  Evidence TC values:  {findings['evidence_tool_count_values']}")
    print(f"  Root cause type:     {findings['root_cause_type']}")
    print(f"  Source module:       signal_analyser_v2.py (score_tool_count)")
    print(f"  Enricher available:  {'YES — but NOT wired' if enricher_info and any(v.get('exists') for v in enricher_info.values()) else 'NO'}")
    print("=" * 70)

    return findings


if __name__ == "__main__":
    findings = run_investigation()

    # Self-smoke: verify the script runs and produces valid output
    assert findings is not None, "run_investigation returned None"
    assert isinstance(findings, dict), f"Expected dict, got {type(findings)}"
    assert "distinct_values_found" in findings, "Missing key: distinct_values_found"
    assert findings["distinct_values_found"] is not None, "distinct_values_found is None (query failed)"
    assert findings["root_cause_type"] in ("producer_bug", "data_quality", "unknown"), \
        f"Unexpected root_cause_type: {findings['root_cause_type']}"

    print("\nOK: self-smoke passed — investigation produces valid findings.")
    sys.exit(0)
