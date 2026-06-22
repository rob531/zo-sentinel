#!/usr/bin/env python3
# deps: requests
"""
Diagnostic for known_bad_pattern weak signal discrimination.

Investigates why known_bad_pattern signal shows only 2 distinct values
(range 69.0-95.0) across the corpus, indicating poor discrimination.

Queries:
1. mcp_signal_scores WHERE signal_name='known_bad_pattern' - score distribution
2. mcp_threat_associations - what threat data feeds this signal
3. Scoring variance analysis

Reports findings as JSON to shared/outputs/goose/
"""

import json
import math
import statistics
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
OUTPUT_PATH = "/home/workspace/zo_sentinel/shared/outputs/goose/diagnose_known_bad_pattern_weak_signal.json"


def ws_query(sql: str, params: list = None) -> list[dict]:
    """Query write_service and return rows as list of dicts."""
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params

    try:
        req = urllib.request.Request(
            f"{WRITE_SERVICE_URL}/query",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(f"Query error: {result['error']}")
            return result if isinstance(result, list) else []
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Query failed: {e}") from e


def compute_stats(scores: list[float]) -> dict[str, Any]:
    """Compute distribution statistics for a list of scores."""
    if not scores:
        return {"error": "No scores provided"}

    n = len(scores)
    distinct = len(set(scores))
    min_val = min(scores)
    max_val = max(scores)
    range_val = max_val - min_val

    # Standard deviation
    mean_val = statistics.mean(scores)
    stdev = statistics.stdev(scores) if n > 1 else 0.0

    # Median and quartiles
    sorted_scores = sorted(scores)
    median = statistics.median(scores)
    q1 = sorted_scores[n // 4] if n > 0 else None
    q3 = sorted_scores[3 * n // 4] if n > 0 else None

    # Coefficient of variation (relative spread)
    cv = (stdev / mean_val) if mean_val > 0 else 0.0

    # Value frequency distribution
    freq: dict[str, int] = {}
    for s in scores:
        key = f"{s:.2f}"
        freq[key] = freq.get(key, 0) + 1

    return {
        "count": n,
        "distinct_values": distinct,
        "min": round(min_val, 4),
        "max": round(max_val, 4),
        "range": round(range_val, 4),
        "mean": round(mean_val, 4),
        "median": round(median, 4),
        "stdev": round(stdev, 4),
        "cv": round(cv, 4),
        "quartile_25": round(q1, 4) if q1 is not None else None,
        "quartile_75": round(q3, 4) if q3 is not None else None,
        "value_distribution": freq,
    }


def diagnose() -> dict[str, Any]:
    """
    Run all diagnostics and return findings as a structured dict.
    """
    findings: dict[str, Any] = {
        "diagnostic": "known_bad_pattern_weak_signal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal": "known_bad_pattern",
        "tables_checked": ["mcp_signal_scores", "mcp_threat_associations"],
        "queries": {},
        "score_distribution": {},
        "threat_associations": {},
        "variance_analysis": {},
        "recommendations": [],
    }

    # =================================================================
    # Query 1: Score distribution from mcp_signal_scores
    # =================================================================
    try:
        sql = """
            SELECT score, COUNT(*) as cnt
            FROM mcp_signal_scores
            WHERE signal_name = 'known_bad_pattern'
            GROUP BY score
            ORDER BY score
        """
        rows = ws_query(sql)
        findings["queries"]["score_distribution"] = {
            "sql": sql.strip(),
            "rows_returned": len(rows),
        }

        if rows:
            scores = [float(r["score"]) for r in rows if r.get("score") is not None]
            count_per_value = {str(r["score"]): r["cnt"] for r in rows}

            findings["score_distribution"] = {
                "scores": scores,
                "count_per_value": count_per_value,
                "stats": compute_stats(scores),
            }
        else:
            findings["score_distribution"] = {"error": "No rows returned"}
            findings["recommendations"].append(
                "No known_bad_pattern rows in mcp_signal_scores - signal may never have been written"
            )

    except Exception as e:
        findings["queries"]["score_distribution"] = {"error": str(e)}
        findings["recommendations"].append(f"Failed to query scores: {e}")

    # =================================================================
    # Query 2: All known_bad_pattern signal rows (sample)
    # =================================================================
    try:
        sql = """
            SELECT server_id, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE signal_name = 'known_bad_pattern'
            ORDER BY scored_at DESC
            LIMIT 20
        """
        rows = ws_query(sql)
        findings["queries"]["sample_rows"] = {
            "sql": sql.strip(),
            "rows_returned": len(rows),
            "sample": rows,
        }
    except Exception as e:
        findings["queries"]["sample_rows"] = {"error": str(e)}

    # =================================================================
    # Query 3: Threat associations for known_bad_pattern
    # =================================================================
    try:
        # Check what threat_types exist and their counts
        sql_threats = """
            SELECT threat_type, severity, source, COUNT(*) as cnt
            FROM mcp_threat_associations
            GROUP BY threat_type, severity, source
            ORDER BY cnt DESC
        """
        threat_rows = ws_query(sql_threats)
        findings["queries"]["threat_associations"] = {
            "sql": sql_threats.strip(),
            "total_rows": len(threat_rows),
            "rows": threat_rows,
        }

        # Specific known_bad_pattern threats
        sql_kbp_threats = """
            SELECT server_id, threat_type, severity, source, evidence, reported_at
            FROM mcp_threat_associations
            WHERE LOWER(threat_type) LIKE '%known_bad%'
               OR LOWER(threat_type) LIKE '%known_bad_pattern%'
               OR LOWER(evidence) LIKE '%known_bad%'
            ORDER BY reported_at DESC
            LIMIT 10
        """
        kbp_threats = ws_query(sql_kbp_threats)
        findings["threat_associations"] = {
            "known_bad_pattern_threats": kbp_threats,
            "count": len(kbp_threats),
        }

        if len(kbp_threats) < 5:
            findings["recommendations"].append(
                f"Only {len(kbp_threats)} known_bad_pattern threat associations found - "
                "threat feed may be underpopulated"
            )

    except Exception as e:
        findings["queries"]["threat_associations"] = {"error": str(e)}
        findings["recommendations"].append(f"Failed to query threats: {e}")

    # =================================================================
    # Variance Analysis
    # =================================================================
    if "scores" in findings["score_distribution"] and findings["score_distribution"]["scores"]:
        scores = findings["score_distribution"]["scores"]
        stats = findings["score_distribution"]["stats"]

        findings["variance_analysis"] = {
            "distinct_count": stats["distinct_values"],
            "cv": stats["cv"],
            "range": stats["range"],
            "stdev": stats["stdev"],
        }

        # Discrimination quality thresholds
        if stats["distinct_values"] <= 2:
            findings["variance_analysis"]["diagnosis"] = "CRITICAL: Only 2 distinct score values - no discrimination"
            findings["recommendations"].append(
                "Score function is producing only 2 distinct values. "
                "This indicates the scoring logic is collapsing variance. "
                "Check if the enricher is using hardcoded buckets or binary logic."
            )
        elif stats["distinct_values"] <= 5:
            findings["variance_analysis"]["diagnosis"] = "WARNING: Low discrimination (<=5 distinct values)"
            findings["recommendations"].append(
                "Low score diversity suggests the enricher may be using too few buckets "
                "or not incorporating enough input features."
            )

        if stats["cv"] < 0.05:
            findings["variance_analysis"]["diagnosis"] = "WARNING: Very low coefficient of variation"
            findings["recommendations"].append(
                "Coefficient of variation < 0.05 means scores are bunched together. "
                "Consider expanding the scoring logic to use more granular features."
            )

        if stats["range"] < 10:
            findings["recommendations"].append(
                f"Score range of only {stats['range']:.1f} points is too narrow. "
                "A healthy signal should span at least 30-50 points."
            )

    # =================================================================
    # Check for evidence blob variance
    # =================================================================
    try:
        sql_evidence = """
            SELECT evidence, COUNT(*) as cnt
            FROM mcp_signal_scores
            WHERE signal_name = 'known_bad_pattern'
              AND evidence IS NOT NULL
              AND evidence != ''
            GROUP BY evidence
            LIMIT 50
        """
        evidence_rows = ws_query(sql_evidence)
        findings["queries"]["evidence_variance"] = {
            "sql": sql_evidence.strip(),
            "distinct_evidence": len(evidence_rows),
            "sample": evidence_rows[:5] if evidence_rows else [],
        }

        if evidence_rows and len(evidence_rows) <= 3:
            findings["recommendations"].append(
                f"Only {len(evidence_rows)} distinct evidence blobs - "
                "evidence may be hardcoded or not capturing per-server variation."
            )

    except Exception as e:
        findings["queries"]["evidence_variance"] = {"error": str(e)}

    # =================================================================
    # Cross-reference: Do servers with threats have higher scores?
    # =================================================================
    try:
        sql_cross = """
            SELECT
                CASE WHEN t.server_id IS NOT NULL THEN 'has_threat' ELSE 'no_threat' END as bucket,
                COUNT(DISTINCT s.server_id) as server_count,
                AVG(s.score) as avg_score,
                MIN(s.score) as min_score,
                MAX(s.score) as max_score
            FROM mcp_signal_scores s
            LEFT JOIN mcp_threat_associations t
                ON s.server_id = t.server_id
                AND (LOWER(t.threat_type) LIKE '%known_bad%' OR LOWER(t.evidence) LIKE '%known_bad%')
            WHERE s.signal_name = 'known_bad_pattern'
            GROUP BY CASE WHEN t.server_id IS NOT NULL THEN 'has_threat' ELSE 'no_threat' END
        """
        cross_rows = ws_query(sql_cross)
        findings["queries"]["threat_score_correlation"] = {
            "sql": sql_cross.strip(),
            "rows": cross_rows,
        }

        if len(cross_rows) == 2:
            threat_bucket = next((r for r in cross_rows if r["bucket"] == "has_threat"), None)
            no_threat_bucket = next((r for r in cross_rows if r["bucket"] == "no_threat"), None)
            if threat_bucket and no_threat_bucket:
                score_diff = abs(threat_bucket["avg_score"] - no_threat_bucket["avg_score"])
                findings["variance_analysis"]["threat_vs_no_threat_gap"] = round(score_diff, 2)
                if score_diff < 5:
                    findings["recommendations"].append(
                        f"Threat/No-threat score gap is only {score_diff:.1f} points. "
                        "The signal is not discriminating based on threat presence."
                    )

    except Exception as e:
        findings["queries"]["threat_score_correlation"] = {"error": str(e)}

    return findings


def main() -> int:
    """Run diagnostic and write JSON output."""
    print("=" * 60)
    print("known_bad_pattern_weak_signal diagnostic")
    print("=" * 60)

    try:
        findings = diagnose()
    except Exception as e:
        findings = {
            "diagnostic": "known_bad_pattern_weak_signal",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }

    # Write JSON output
    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(findings, f, indent=2, default=str)
        print(f"\nOutput written to: {OUTPUT_PATH}")
    except Exception as e:
        print(f"Warning: Could not write output file: {e}")
        # Print to stdout as fallback
        print(json.dumps(findings, indent=2, default=str))

    # Print summary
    print("\n--- SUMMARY ---")
    if "error" in findings:
        print(f"ERROR: {findings['error']}")
    else:
        stats = findings.get("score_distribution", {}).get("stats", {})
        print(f"Signal rows analyzed: {stats.get('count', 'N/A')}")
        print(f"Distinct score values: {stats.get('distinct_values', 'N/A')}")
        print(f"Score range: {stats.get('min', 'N/A')} - {stats.get('max', 'N/A')}")
        print(f"Coefficient of variation: {stats.get('cv', 'N/A')}")

        threat_count = findings.get("threat_associations", {}).get("count", 0)
        print(f"Threat associations: {threat_count}")

        if findings.get("recommendations"):
            print("\nRecommendations:")
            for i, rec in enumerate(findings["recommendations"], 1):
                print(f"  {i}. {rec}")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
