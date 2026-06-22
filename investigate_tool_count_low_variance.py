#!/usr/bin/env python3
# deps: requests
"""
investigate_tool_count_low_variance.py

Diagnostic utility to investigate why tool_count signal has only 2 distinct
values across all MCP servers. Addresses the WEAK signal quality flag.

All DB access goes through write_service HTTP API (127.0.0.1:8772).
NEVER import duckdb directly.
"""

import requests
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
import sys

WRITE_SERVICE = "http://127.0.0.1:8772"
TIMEOUT = 15


def ws_query(sql: str, params: Optional[List] = None) -> List[Dict]:
    """Query write_service for SELECT statements."""
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_execute(sql: str, params: Optional[List] = None) -> None:
    """Execute DDL/DML via write_service."""
    payload = {"sql": sql, "params": params or [], "wait": True}
    resp = requests.post(f"{WRITE_SERVICE}/execute", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()


def log(msg: str) -> None:
    """Print timestamped message."""
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] {msg}")


class ToolCountInvestigator:
    """Investigates low variance in tool_count signal."""

    def __init__(self):
        self.findings: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_type": "tool_count",
            "data_collection": {},
            "score_distribution": {},
            "scanner_data": {},
            "signal_comparison": {},
            "sample_records": [],
            "root_cause": "UNKNOWN",
            "root_cause_explanation": [],
            "recommendations": []
        }

    def query_tool_count_scores(self) -> List[Dict]:
        """Query all tool_count records from mcp_signal_scores."""
        log("Querying tool_count scores from mcp_signal_scores...")

        sql = """
        SELECT
            mss.id,
            mss.mcp_server_id,
            ms.name as server_name,
            ms.url,
            mss.signal_type,
            mss.signal_value as raw_value,
            mss.normalized_score,
            mss.confidence,
            mss.created_at
        FROM mcp_signal_scores mss
        LEFT JOIN mcp_servers ms ON mss.mcp_server_id = ms.id
        WHERE mss.signal_type = 'tool_count'
        ORDER BY mss.normalized_score DESC
        """

        rows = ws_query(sql)
        log(f"  Found {len(rows)} tool_count records")
        self.findings["data_collection"]["mcp_signal_scores_count"] = len(rows)
        return rows

    def query_scanner_tool_counts(self) -> List[Dict]:
        """Query raw tool counts from mcp_servers table (scanner output)."""
        log("Querying scanner tool counts from mcp_servers...")

        sql = """
        SELECT
            id,
            name,
            url,
            tool_count,
            tools_detected,
            created_at
        FROM mcp_servers
        WHERE tool_count IS NOT NULL
        ORDER BY tool_count DESC
        """

        rows = ws_query(sql)
        log(f"  Found {len(rows)} servers with tool_count")
        self.findings["data_collection"]["scanner_tool_counts"] = len(rows)
        return rows

    def query_all_signal_stats(self) -> List[Dict]:
        """Query stats for all signal types for comparison."""
        log("Querying all signal type statistics...")

        sql = """
        SELECT
            signal_type,
            COUNT(*) as record_count,
            COUNT(DISTINCT normalized_score) as distinct_scores,
            MIN(normalized_score) as min_score,
            MAX(normalized_score) as max_score,
            AVG(normalized_score) as avg_score
        FROM mcp_signal_scores
        GROUP BY signal_type
        ORDER BY distinct_scores ASC
        """

        rows = ws_query(sql)
        log(f"  Found stats for {len(rows)} signal types")
        return rows

    def analyze_distribution(self, scores: List[Dict]) -> Dict[str, Any]:
        """Analyze distribution of normalized scores."""
        if not scores:
            return {"error": "No scores found", "distinct_normalized": 0, "distinct_raw": 0}

        normalized_scores = [s["normalized_score"] for s in scores]
        raw_values = [s["raw_value"] for s in scores]

        score_counter = Counter(normalized_scores)
        raw_counter = Counter(raw_values)

        analysis = {
            "total_records": len(scores),
            "distinct_normalized": len(score_counter),
            "distinct_raw": len(raw_counter),
            "score_distribution": dict(score_counter.most_common()),
            "raw_distribution": dict(raw_counter.most_common()),
            "min_score": min(normalized_scores),
            "max_score": max(normalized_scores),
        }

        self.findings["score_distribution"] = analysis
        return analysis

    def identify_root_cause(
        self,
        score_analysis: Dict,
        scanner_data: List[Dict],
        all_signal_stats: List[Dict]
    ) -> Tuple[str, List[str]]:
        """Identify the root cause of low variance."""
        findings = []
        distinct_scores = score_analysis.get("distinct_normalized", 0)
        distinct_raw = score_analysis.get("distinct_raw", 0)
        total_records = score_analysis.get("total_records", 0)

        # Get tool_count stats from all signals
        tool_count_stats = next(
            (s for s in all_signal_stats if s["signal_type"] == "tool_count"),
            None
        )

        # Calculate average distinct values for other signals
        other_signals = [s for s in all_signal_stats if s["signal_type"] != "tool_count"]
        avg_distinct = (
            sum(s["distinct_scores"] for s in other_signals) / len(other_signals)
            if other_signals else 0
        )

        if total_records == 0:
            issue_type = "SCANNER_NOT_EXTRACTING"
            findings.append("❌ No tool_count records found in mcp_signal_scores table")
            findings.append("   → Scanner may not be extracting tool counts at all")
            findings.append("   → Check if MCP server detection is working")
            findings.append("   → Verify tools endpoint is being called correctly")

        elif distinct_scores == 1:
            if distinct_raw > 1:
                issue_type = "SIGNAL_ANALYSER_COLLAPSING"
                findings.append(f"⚠️  Only 1 distinct normalized score across {total_records} records")
                findings.append(f"   → But raw values have {distinct_raw} distinct values!")
                findings.append(f"   → Raw distribution: {score_analysis['raw_distribution']}")
                findings.append("   → This indicates SIGNAL_ANALYSER is collapsing scores")
                findings.append("   → Review the normalization formula for tool_count")
            else:
                issue_type = "SCANNER_NO_VARIANCE"
                findings.append(f"⚠️  Only 1 distinct normalized score across {total_records} records")
                findings.append(f"   → Raw values also have no variance: {score_analysis['raw_distribution']}")
                findings.append("   → This indicates SCANNER is not extracting distinct tool counts")
                findings.append("   → Check scanner tool detection logic")

        elif distinct_scores == 2:
            issue_type = "THRESHOLD_OR_DATA_QUALITY"
            findings.append(f"⚠️  Only 2 distinct normalized scores across {total_records} records")
            findings.append(f"   → Score distribution: {score_analysis['score_distribution']}")
            findings.append(f"   → Raw distribution: {score_analysis['raw_distribution']}")

            if distinct_raw > distinct_scores:
                findings.append("   → Raw values have more variance than normalized scores")
                findings.append("   → Signal analyser may be using threshold-based binning")
            elif distinct_raw == distinct_scores:
                findings.append("   → Raw values match normalized variance")
                findings.append("   → Scanner is extracting values, but they're mostly similar")
                if scanner_data:
                    tool_counts = [s.get("tool_count") for s in scanner_data if s.get("tool_count") is not None]
                    if tool_counts:
                        findings.append(f"   → Scanner raw tool_counts: {list(set(tool_counts))}")

            if tool_count_stats and tool_count_stats["record_count"] > 0:
                if avg_distinct > distinct_scores * 2:
                    findings.append(f"   → Other signals average {avg_distinct:.1f} distinct values")
                    findings.append("   → This suggests tool_count calculation is too simplistic")
                    findings.append("   → Consider logarithmic or percentile-based scoring")

        else:
            issue_type = "OK"
            findings.append(f"✅ tool_count signal has {distinct_scores} distinct values (acceptable)")

        self.findings["root_cause"] = issue_type
        self.findings["root_cause_explanation"] = findings
        return issue_type, findings

    def generate_recommendations(self, issue_type: str) -> List[str]:
        """Generate recommendations based on issue type."""
        recommendations = []

        if issue_type == "SCANNER_NOT_EXTRACTING":
            recommendations = [
                "1. Check MCP server scanner implementation (mcp_scanner.py)",
                "2. Verify tools endpoint is being called correctly",
                "3. Check if tools JSON parsing is working",
                "4. Look for errors in scanner logs",
                "5. Ensure tool_count field is being written to mcp_servers table"
            ]
        elif issue_type == "SCANNER_NO_VARIANCE":
            recommendations = [
                "1. Review scanner tool detection logic",
                "2. Check if all MCP servers return the same tool list",
                "3. Verify tools are being parsed correctly from responses",
                "4. Look for hardcoded or fallback values"
            ]
        elif issue_type == "SIGNAL_ANALYSER_COLLAPSING":
            recommendations = [
                "1. Review signal_analyser.py tool_count calculation",
                "2. Check if scores are being thresholded incorrectly",
                "3. Verify normalization formula isn't collapsing values",
                "4. Compare with other signal calculations for reference",
                "5. Ensure diverse raw values map to diverse normalized scores"
            ]
        elif issue_type == "THRESHOLD_OR_DATA_QUALITY":
            recommendations = [
                "1. Review why tool counts are clustered in 2 values",
                "2. Consider using logarithmic scaling for tool counts",
                "3. Check if server classification is too binary",
                "4. Verify MCP servers actually have different tool counts",
                "5. Implement percentile-based scoring for better discrimination"
            ]
        else:
            recommendations = ["• No action needed - signal variance is acceptable"]

        self.findings["recommendations"] = recommendations
        return recommendations

    def generate_report(self) -> str:
        """Generate formatted text report."""
        lines = []
        ts = datetime.now(timezone.utc).isoformat()

        lines.append("=" * 70)
        lines.append("TOOL_COUNT SIGNAL VARIANCE INVESTIGATION REPORT")
        lines.append(f"Generated: {ts}")
        lines.append("=" * 70)
        lines.append("")

        # Section 1: Data Collection
        lines.append("📊 SECTION 1: DATA COLLECTION")
        lines.append("-" * 40)
        dc = self.findings.get("data_collection", {})
        lines.append(f"  • mcp_signal_scores tool_count records: {dc.get('mcp_signal_scores_count', 'N/A')}")
        lines.append(f"  • mcp_servers with tool_count: {dc.get('scanner_tool_counts', 'N/A')}")
        lines.append("")

        # Section 2: Score Distribution
        lines.append("📈 SECTION 2: TOOL_COUNT SCORE DISTRIBUTION")
        lines.append("-" * 40)
        dist = self.findings.get("score_distribution", {})

        if "error" in dist:
            lines.append(f"  ❌ Error: {dist['error']}")
        else:
            lines.append(f"  Total records: {dist.get('total_records', 0)}")
            lines.append(f"  Distinct normalized scores: {dist.get('distinct_normalized', 0)}")
            lines.append(f"  Distinct raw values: {dist.get('distinct_raw', 0)}")
            lines.append(f"  Score range: [{dist.get('min_score', 'N/A')}, {dist.get('max_score', 'N/A')}]")
            lines.append("")
            lines.append("  Normalized score distribution:")
            for score, count in dist.get("score_distribution", {}).items():
                pct = (count / dist["total_records"]) * 100 if dist["total_records"] else 0
                lines.append(f"    {score}: {count} records ({pct:.1f}%)")
            lines.append("")
            lines.append("  Raw value distribution:")
            for value, count in dist.get("raw_distribution", {}).items():
                pct = (count / dist["total_records"]) * 100 if dist["total_records"] else 0
                lines.append(f"    {value}: {count} records ({pct:.1f}%)")
        lines.append("")

        # Section 3: Comparison with Other Signals
        lines.append("🔄 SECTION 3: COMPARISON WITH OTHER SIGNALS")
        lines.append("-" * 40)
        all_stats = self.findings.get("signal_comparison", {}).get("all_stats", [])
        for sig in all_stats:
            status = "✓" if sig["distinct_scores"] > 2 else "⚠"
            lines.append(
                f"  {status} {sig['signal_type']}: {sig['distinct_scores']} distinct scores "
                f"(range: {sig['min_score']:.2f} - {sig['max_score']:.2f})"
            )
        lines.append("")

        # Section 4: Sample Records
        lines.append("📋 SECTION 4: SAMPLE RECORDS")
        lines.append("-" * 40)
        samples = self.findings.get("sample_records", [])
        if samples:
            for rec in samples[:5]:
                lines.append(
                    f"  • {rec.get('server_name', 'Unknown')}: raw={rec.get('raw_value')}, "
                    f"score={rec.get('normalized_score')}, conf={rec.get('confidence')}"
                )
        else:
            lines.append("  (no sample records available)")
        lines.append("")

        # Section 5: Root Cause Analysis
        lines.append("🔍 SECTION 5: ROOT CAUSE ANALYSIS")
        lines.append("-" * 40)
        lines.append(f"  ISSUE TYPE: {self.findings.get('root_cause', 'UNKNOWN')}")
        lines.append("")
        for finding in self.findings.get("root_cause_explanation", []):
            lines.append(f"  {finding}")
        lines.append("")

        # Section 6: Recommendations
        lines.append("💡 SECTION 6: RECOMMENDATIONS")
        lines.append("-" * 40)
        for rec in self.findings.get("recommendations", []):
            lines.append(f"  {rec}")
        lines.append("")
        lines.append("=" * 70)
        lines.append("END OF REPORT")
        lines.append("=" * 70)

        return "\n".join(lines)

    def run(self) -> bool:
        """Run the investigation."""
        log("Starting tool_count low variance investigation...")

        try:
            # Collect data
            tool_count_scores = self.query_tool_count_scores()
            scanner_data = self.query_scanner_tool_counts()
            all_signal_stats = self.query_all_signal_stats()

            # Store comparison data
            self.findings["signal_comparison"]["all_stats"] = all_signal_stats

            # Store sample records
            self.findings["sample_records"] = tool_count_scores[:10]

            # Analyze distribution
            score_analysis = self.analyze_distribution(tool_count_scores)

            # Identify root cause
            issue_type, _ = self.identify_root_cause(score_analysis, scanner_data, all_signal_stats)

            # Generate recommendations
            self.generate_recommendations(issue_type)

            # Output report
            report = self.generate_report()
            print(report)

            # Machine-readable summary to stderr
            print(
                f"\n[Summary] distinct_scores={score_analysis.get('distinct_normalized', 0)}, "
                f"issue_type={issue_type}",
                file=sys.stderr
            )

            # Exit with non-zero if issue found
            return issue_type == "OK"

        except requests.exceptions.RequestException as e:
            log(f"Request error: {e}")
            print(f"Database access error: {e}", file=sys.stderr)
            return False
        except Exception as e:
            log(f"Unexpected error: {e}")
            print(f"Error: {e}", file=sys.stderr)
            return False


def main() -> int:
    """Main entry point."""
    investigator = ToolCountInvestigator()
    success = investigator.run()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
