#!/usr/bin/env python3
"""
diagnose_mcp_signal_enrichments_low_volume.py

Diagnostic utility to investigate why mcp_signal_enrichments has only ~12 rows
across 1754 servers. Read-only — does NOT write to any table.

Reads: mcp_signal_enrichments, mcp_signal_scores, mcp_server_registry.
No output files.
"""

import requests
import sys
import time

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def query_service(sql: str, params: list | None = None) -> dict:
    """Query write_service /query endpoint with 10s timeout."""
    payload: dict = {"sql": sql}
    if params is not None:
        payload["params"] = params
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def count_rows(table: str) -> int:
    """Count total rows in a table."""
    sql = f"SELECT COUNT(*) AS cnt FROM {table}"
    result = query_service(sql)
    rows = result.get("rows", [])
    if not rows:
        return 0
    return int(rows[0].get("cnt", 0))


def enrichment_distribution() -> dict:
    """Count rows per enrichment_type in mcp_signal_enrichments."""
    sql = """
        SELECT enrichment_type, COUNT(*) AS cnt
        FROM mcp_signal_enrichments
        GROUP BY enrichment_type
        ORDER BY cnt DESC
    """
    result = query_service(sql)
    return {row["enrichment_type"]: int(row["cnt"]) for row in result.get("rows", [])}


def servers_with_enrichments() -> set:
    """Return set of server_ids that have at least one enrichment row."""
    sql = "SELECT DISTINCT server_id FROM mcp_signal_enrichments"
    result = query_service(sql)
    return {row["server_id"] for row in result.get("rows", [])}


def servers_with_scores() -> set:
    """Return set of server_ids that have at least one signal score row."""
    sql = "SELECT DISTINCT server_id FROM mcp_signal_scores"
    result = query_service(sql)
    return {row["server_id"] for row in result.get("rows", [])}


def registry_servers() -> set:
    """Return set of all server_ids in the registry."""
    sql = "SELECT server_id FROM mcp_server_registry"
    result = query_service(sql)
    return {row["server_id"] for row in result.get("rows", [])}


def scores_per_enrichment_type() -> dict:
    """Count rows per signal_type in mcp_signal_scores."""
    sql = """
        SELECT signal_type, COUNT(*) AS cnt
        FROM mcp_signal_scores
        GROUP BY signal_type
        ORDER BY cnt DESC
    """
    result = query_service(sql)
    return {row["signal_type"]: int(row["cnt"]) for row in result.get("rows", [])}


def check_writer_target() -> str | None:
    """
    Inspect enrichments_writer_daemon.py source to find which table it writes to.
    Returns the table name or None if not determinable from file read.
    """
    try:
        with open(
            "/home/workspace/zo_sentinel/enrichments_writer_daemon.py", "r", encoding="utf-8"
        ) as fh:
            content = fh.read()
        # Look for the table= key in the write payload
        for line in content.splitlines():
            if '"table"' in line and ":" in line:
                return line.strip()
    except FileNotFoundError:
        pass
    return None


def check_enricher_evidence_target(enricher_file: str) -> list[str]:
    """
    Scan an enricher file for references to table names or evidence_blob structure.
    Returns a list of detected table write patterns.
    """
    targets: list[str] = []
    try:
        with open(enricher_file, "r", encoding="utf-8") as fh:
            content = fh.read()
        for line in content.splitlines():
            lower = line.lower()
            if any(k in lower for k in ['"table"', "'table'", "table=", "table :"]):
                if "write" in lower or "service" in lower:
                    targets.append(line.strip())
            if "evidence_blob" in lower:
                targets.append(line.strip())
    except FileNotFoundError:
        pass
    return targets


def diagnose() -> dict:
    """
    Run all diagnostic checks and return findings dict.

    Returns:
        {
            "enrichment_count": int,
            "expected_count": int,
            "missing_servers_pct": float,
            "suspected_pipeline_gap": str,
            "recommended_directive": str,
            "details": dict,
        }
    """
    findings: dict = {
        "details": {},
        "enrichment_count": 0,
        "expected_count": 0,
        "missing_servers_pct": 0.0,
        "suspected_pipeline_gap": "",
        "recommended_directive": "",
    }

    # 1. mcp_signal_enrichments row count and distribution
    enrichment_total = count_rows("mcp_signal_enrichments")
    enrichment_dist = enrichment_distribution()
    findings["enrichment_count"] = enrichment_total
    findings["details"]["mcp_signal_enrichments_total"] = enrichment_total
    findings["details"]["mcp_signal_enrichments_dist"] = enrichment_dist

    # 2. mcp_server_registry row count
    registry_total = count_rows("mcp_server_registry")
    findings["expected_count"] = registry_total
    findings["details"]["mcp_server_registry_total"] = registry_total

    # 3. servers with enrichments vs registry
    servers_enriched = servers_with_enrichments()
    servers_reg = registry_servers()
    missing_servers = servers_reg - servers_enriched
    missing_pct = (
        round(len(missing_servers) / len(servers_reg) * 100, 2)
        if servers_reg
        else 0.0
    )
    findings["missing_servers_pct"] = missing_pct
    findings["details"]["servers_with_enrichments"] = len(servers_enriched)
    findings["details"]["servers_missing_enrichments"] = len(missing_servers)

    # 4. Check what enrichments_writer_daemon writes to
    writer_target = check_writer_target()
    findings["details"]["writer_target_line"] = writer_target

    # 5. Check enricher evidence_blob patterns
    community_targets = check_enricher_evidence_target(
        "/home/workspace/zo_sentinel/community_signal_enrichment.py"
    )
    tool_count_targets = check_enricher_evidence_target(
        "/home/workspace/zo_sentinel/tool_count_enrichment.py"
    )
    findings["details"]["community_signal_enrichment_evidence_lines"] = community_targets
    findings["details"]["tool_count_enrichment_evidence_lines"] = tool_count_targets

    # 6. mcp_signal_scores — are signals being recorded independently?
    scores_total = count_rows("mcp_signal_scores")
    scores_dist = scores_per_enrichment_type()
    servers_scored = servers_with_scores()
    findings["details"]["mcp_signal_scores_total"] = scores_total
    findings["details"]["mcp_signal_scores_dist"] = scores_dist
    findings["details"]["servers_with_scores"] = len(servers_scored)
    findings["details"]["servers_scored_not_enriched"] = len(
        servers_scored - servers_enriched
    )
    findings["details"]["servers_enriched_not_scored"] = len(
        servers_enriched - servers_scored
    )

    # 7. Determine suspected pipeline gap and recommended directive
    if enrichment_total == 0:
        findings["suspected_pipeline_gap"] = (
            "mcp_signal_enrichments is EMPTY — no enricher has written any rows. "
            "The enrichment pipeline may not be running, or enrichers are writing "
            "to a different table (check evidence_blob destination)."
        )
        findings["recommended_directive"] = (
            "investigate_enrichment_pipeline_staleness"
        )
    elif enrichment_total < 100:
        # Only ~12 rows when registry has 1754 servers
        if len(servers_enriched) < len(servers_reg):
            findings["suspected_pipeline_gap"] = (
                f"Only {enrichment_total} enrichment rows for {registry_total} servers. "
                f"{len(servers_enriched)} servers have enrichments; "
                f"{len(missing_servers)} ({missing_pct}%) are missing. "
                "enrichments_writer_daemon writes to mcp_signal_scores, not "
                "mcp_signal_enrichments — verify enrichers feed the correct table."
            )
        else:
            findings["suspected_pipeline_gap"] = (
                f"Only {enrichment_total} enrichment rows and all {len(servers_enriched)} "
                "servers are covered, but row count is far below expected "
                f"({registry_total} servers × N enrichment_types). "
                "Enrichment pipeline may be running only once or has been "
                "back-filled then halted."
            )
        findings["recommended_directive"] = (
            "investigate_enrichment_coverage_gap"
        )
    else:
        findings["suspected_pipeline_gap"] = (
            f"Enrichment count {enrichment_total} is non-trivial. "
            "Pipeline gap may be enrichment-type-specific. "
            "Check enrichment_dist for which types are missing."
        )
        findings["recommended_directive"] = (
            "enrichment_pipeline_auditor"
        )

    # Overwrite gap if writer targets mcp_signal_scores
    if writer_target and "mcp_signal_scores" in str(writer_target):
        findings["suspected_pipeline_gap"] = (
            "enrichments_writer_daemon writes to mcp_signal_scores (not mcp_signal_enrichments). "
            "Enrichers should target mcp_signal_enrichments directly, or a separate "
            "writer daemon must populate mcp_signal_enrichments. "
            f"Current writer target: {writer_target}"
        )
        findings["recommended_directive"] = (
            "build_mcp_signal_enrichments_writer_daemon"
        )

    return findings


def print_findings(f: dict) -> None:
    """Pretty-print findings to stdout."""
    print("=" * 70)
    print("DIAGNOSTIC: mcp_signal_enrichments Low Volume")
    print("=" * 70)

    print(f"\n[1] mcp_signal_enrichments row count: {f['enrichment_count']}")
    dist = f["details"].get("mcp_signal_enrichments_dist", {})
    if dist:
        print("     Distribution per enrichment_type:")
        for etype, cnt in dist.items():
            print(f"       {etype}: {cnt}")
    else:
        print("     (empty)")

    print(f"\n[2] mcp_server_registry row count: {f['expected_count']} (expected ~{f['expected_count']} per enrichment_type)")
    print(f"    Servers with enrichments: {f['details'].get('servers_with_enrichments', 0)}")
    print(f"    Servers missing enrichments: {f['details'].get('servers_missing_enrichments', 0)}")
    print(f"    Missing servers pct: {f['missing_servers_pct']}%")

    print(f"\n[3] enrichments_writer_daemon target table:")
    writer_line = f["details"].get("writer_target_line", "NOT FOUND")
    print(f"     {writer_line}")

    print(f"\n[4a] community_signal_enrichment.py evidence lines:")
    for line in f["details"].get("community_signal_enrichment_evidence_lines", [])[:5]:
        print(f"       {line}")
    print(f"\n[4b] tool_count_enrichment.py evidence lines:")
    for line in f["details"].get("tool_count_enrichment_evidence_lines", [])[:5]:
        print(f"       {line}")

    print(f"\n[5] mcp_signal_scores row count: {f['details'].get('mcp_signal_scores_total', 'N/A')}")
    scores_dist = f["details"].get("mcp_signal_scores_dist", {})
    if scores_dist:
        print("     Distribution per signal_type:")
        for stype, cnt in scores_dist.items():
            print(f"       {stype}: {cnt}")
    print(f"    Servers with scores: {f['details'].get('servers_with_scores', 0)}")
    print(f"    Servers scored but not enriched: {f['details'].get('servers_scored_not_enriched', 0)}")
    print(f"    Servers enriched but not scored: {f['details'].get('servers_enriched_not_scored', 0)}")

    print("\n" + "=" * 70)
    print("FINDINGS SUMMARY")
    print("=" * 70)
    print(f"  enrichment_count:      {f['enrichment_count']}")
    print(f"  expected_count:         {f['expected_count']}")
    print(f"  missing_servers_pct:    {f['missing_servers_pct']}%")
    print(f"  suspected_pipeline_gap: {f['suspected_pipeline_gap']}")
    print(f"  recommended_directive:  {f['recommended_directive']}")
    print("=" * 70)


if __name__ == "__main__":
    try:
        findings = diagnose()
        print_findings(findings)
        # Gate: if enrichment_count is suspiciously low (< 100 when registry > 1000)
        if findings["enrichment_count"] < 100 and findings["expected_count"] > 1000:
            print(
                f"\nWARNING: enrichment_count={findings['enrichment_count']} is "
                f"{findings['missing_servers_pct']}% below expected "
                f"(expected ~{findings['expected_count']}). "
                f"Recommended action: {findings['recommended_directive']}"
            )
            sys.exit(1)
        sys.exit(0)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: write_service unreachable — {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(3)
