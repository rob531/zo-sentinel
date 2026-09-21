#!/usr/bin/env python3
# deps: requests
"""
investigate_known_bad_pattern_low_variance.py

Diagnostic utility to investigate why known_bad_pattern signal has only 2 distinct 
values across all MCP servers. Addresses the WEAK signal quality flag.

All DB access goes through write_service HTTP API (127.0.0.1:8772).
NEVER import duckdb directly.
"""

import requests
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import json
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


class KnownBadPatternInvestigator:
    def __init__(self):
        self.findings: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_type": "known_bad_pattern",
            "investigation_summary": {},
            "value_distribution": {},
            "server_breakdown": [],
            "threat_intel_analysis": {},
            "detection_logic_health": {},
            "root_causes": [],
            "recommendations": []
        }

    def query_value_distribution(self) -> Dict[str, Any]:
        """Query mcp_signal_scores for known_bad_pattern value distribution."""
        log("Querying value distribution...")
        
        sql = """
        SELECT 
            signal_value,
            COUNT(*) as occurrence_count,
            COUNT(DISTINCT mcp_server_id) as server_count
        FROM mcp_signal_scores
        WHERE signal_type = 'known_bad_pattern'
        GROUP BY signal_value
        ORDER BY signal_value DESC
        """
        
        rows = ws_query(sql)
        
        distribution = {
            "distinct_values": [],
            "total_records": 0,
            "value_breakdown": []
        }
        
        for row in rows:
            value = row.get("signal_value")
            count = row.get("occurrence_count", 0)
            server_count = row.get("server_count", 0)
            
            distribution["distinct_values"].append(value)
            distribution["total_records"] += count
            distribution["value_breakdown"].append({
                "signal_value": value,
                "occurrence_count": count,
                "server_count": server_count,
                "percentage": 0.0
            })
        
        # Calculate percentages
        total = distribution["total_records"]
        for item in distribution["value_breakdown"]:
            if total > 0:
                item["percentage"] = round((item["occurrence_count"] / total) * 100, 2)
        
        log(f"  Found {len(distribution['distinct_values'])} distinct values across {total} records")
        log(f"  Values: {distribution['distinct_values']}")
        
        return distribution

    def query_server_level_breakdown(self) -> List[Dict]:
        """Get per-server signal values for known_bad_pattern."""
        log("Analyzing server-level breakdown...")
        
        sql = """
        SELECT 
            mss.mcp_server_id,
            ms.server_name,
            ms.server_version,
            ms.threat_intel_enabled,
            ms.pattern_matching_strict,
            mss.signal_value,
            mss.last_updated
        FROM mcp_signal_scores mss
        JOIN mcp_servers ms ON mss.mcp_server_id = ms.id
        WHERE mss.signal_type = 'known_bad_pattern'
        ORDER BY mss.signal_value DESC, ms.server_name
        """
        
        rows = ws_query(sql)
        log(f"  Analyzed {len(rows)} server records")
        return rows

    def query_threat_intel_coverage(self) -> Dict[str, Any]:
        """Check threat intelligence coverage for known_bad_pattern detection."""
        log("Checking threat intelligence coverage...")
        
        # Check threat signatures count (if table exists)
        signature_count = 0
        try:
            sql = """
            SELECT COUNT(*) as cnt FROM mcp_threat_associations
            WHERE threat_type LIKE '%known_bad%' OR threat_type LIKE '%pattern%'
            """
            rows = ws_query(sql)
            if rows:
                signature_count = rows[0].get("cnt", 0)
        except Exception:
            pass
        
        # Check servers with threat intel enabled vs disabled
        ti_coverage = {}
        try:
            sql = """
            SELECT 
                ms.threat_intel_enabled,
                COUNT(*) as server_count
            FROM mcp_servers ms
            JOIN mcp_signal_scores mss ON ms.id = mss.mcp_server_id
            WHERE mss.signal_type = 'known_bad_pattern'
            GROUP BY ms.threat_intel_enabled
            """
            rows = ws_query(sql)
            for row in rows:
                ti_coverage[str(row.get("threat_intel_enabled"))] = row.get("server_count", 0)
        except Exception:
            pass
        
        # Check pattern matching configuration correlation
        pattern_config = []
        try:
            sql = """
            SELECT 
                ms.pattern_matching_strict,
                AVG(mss.signal_value) as avg_signal_value,
                COUNT(*) as cnt
            FROM mcp_servers ms
            JOIN mcp_signal_scores mss ON ms.id = mss.mcp_server_id
            WHERE mss.signal_type = 'known_bad_pattern'
            GROUP BY ms.pattern_matching_strict
            """
            rows = ws_query(sql)
            for row in rows:
                pattern_config.append({
                    "strict": row.get("pattern_matching_strict"),
                    "avg_signal": row.get("avg_signal_value"),
                    "count": row.get("cnt")
                })
        except Exception:
            pass
        
        log(f"  Threat associations: {signature_count}")
        log(f"  Threat intel coverage: {ti_coverage}")
        log(f"  Pattern configs: {len(pattern_config)}")
        
        return {
            "threat_signature_count": signature_count,
            "threat_intel_coverage": ti_coverage,
            "pattern_config_analysis": pattern_config
        }

    def query_detection_logic_health(self) -> Dict[str, Any]:
        """Check detection logic firing patterns."""
        log("Checking detection logic health...")
        
        non_firing = []
        try:
            # Get records with zero signal values (detection not firing)
            sql = """
            SELECT 
                signal_value,
                COUNT(*) as count
            FROM mcp_signal_scores
            WHERE signal_type = 'known_bad_pattern'
            GROUP BY signal_value
            HAVING signal_value = 0 OR signal_value IS NULL
            """
            rows = ws_query(sql)
            for row in rows:
                non_firing.append({
                    "value": row.get("signal_value"),
                    "count": row.get("count", 0)
                })
        except Exception:
            pass
        
        # Check detection timing
        latest_detection = None
        earliest_detection = None
        try:
            sql = """
            SELECT 
                MAX(last_updated) as latest_detection,
                MIN(last_updated) as earliest_detection
            FROM mcp_signal_scores
            WHERE signal_type = 'known_bad_pattern'
            """
            rows = ws_query(sql)
            if rows:
                latest_detection = rows[0].get("latest_detection")
                earliest_detection = rows[0].get("earliest_detection")
        except Exception:
            pass
        
        log(f"  Non-firing records (0/null): {len(non_firing)}")
        log(f"  Latest detection: {latest_detection}")
        
        return {
            "non_firing_records": non_firing,
            "latest_detection": latest_detection,
            "earliest_detection": earliest_detection
        }

    def analyze_root_causes(self) -> List[Dict]:
        """Analyze and identify root causes for low variance."""
        causes = []
        
        # Cause 1: Pattern matching too coarse (binary output)
        dist = self.findings["value_distribution"]
        distinct_count = len(dist.get("distinct_values", []))
        
        if distinct_count <= 2:
            causes.append({
                "cause_id": 1,
                "cause_type": "pattern_matching_too_coarse",
                "evidence": f"Only {distinct_count} distinct signal values found",
                "severity": "HIGH",
                "details": "Pattern matching may be producing boolean-style outputs (0/1) rather than nuanced scores"
            })
        
        # Cause 2: Insufficient threat intelligence coverage
        ti = self.findings["threat_intel_analysis"]
        if ti["threat_signature_count"] < 10:
            causes.append({
                "cause_id": 2,
                "cause_type": "insufficient_threat_intelligence_coverage",
                "evidence": f"Only {ti['threat_signature_count']} threat associations configured",
                "severity": "HIGH",
                "details": "Insufficient threat intelligence to differentiate threat levels"
            })
        
        # Cause 3: Detection logic not firing
        dl = self.findings["detection_logic_health"]
        if dl["non_firing_records"]:
            non_firing_count = sum(r["count"] for r in dl["non_firing_records"])
            if non_firing_count > 0:
                causes.append({
                    "cause_id": 3,
                    "cause_type": "detection_logic_not_firing",
                    "evidence": f"{non_firing_count} records with zero/null signal values",
                    "severity": "MEDIUM",
                    "details": "Detection logic may not be triggering for many server configurations"
                })
        
        # Cause 4: Binary pattern configuration
        for config in ti.get("pattern_config_analysis", []):
            avg_sig = config.get("avg_signal")
            if avg_sig is not None and (avg_sig == 0.0 or avg_sig == 1.0 or avg_sig == 100.0):
                causes.append({
                    "cause_id": 4,
                    "cause_type": "binary_pattern_configuration",
                    "evidence": f"Strict={config['strict']} produces avg signal {avg_sig}",
                    "severity": "MEDIUM",
                    "details": "Pattern matching strictness setting may be forcing binary outputs"
                })
        
        return causes

    def generate_recommendations(self) -> List[str]:
        """Generate actionable recommendations based on findings."""
        recommendations = []
        
        for cause in self.findings["root_causes"]:
            ct = cause["cause_type"]
            ti_count = self.findings["threat_intel_analysis"]["threat_signature_count"]
            
            if ct == "pattern_matching_too_coarse":
                recommendations.extend([
                    "Review pattern matching logic to produce graduated scores (0.0-100.0 scale)",
                    "Implement multi-factor scoring combining multiple pattern matches",
                    "Add confidence weighting based on pattern specificity"
                ])
            
            elif ct == "insufficient_threat_intelligence_coverage":
                recommendations.extend([
                    f"Expand threat associations (current: {ti_count})",
                    "Add industry-specific threat patterns (MITRE ATT&CK, CVE databases)",
                    "Implement dynamic threat intelligence feeds",
                    "Create custom patterns for high-value targets"
                ])
            
            elif ct == "detection_logic_not_firing":
                recommendations.extend([
                    "Audit detection trigger conditions",
                    "Verify threat_intel_enabled flag is properly set on servers",
                    "Check for configuration drift in MCP servers",
                    "Review pattern matching engine health"
                ])
            
            elif ct == "binary_pattern_configuration":
                recommendations.extend([
                    "Adjust pattern_matching_strict setting to allow nuanced scoring",
                    "Implement graduated response thresholds instead of binary"
                ])
        
        return list(set(recommendations))

    def run_investigation(self) -> Dict:
        """Execute full investigation and generate report."""
        print("=" * 60)
        print("KNOWN_BAD_PATTERN SIGNAL INVESTIGATION")
        print("=" * 60)
        
        print("\n[1/4] Querying value distribution...")
        self.findings["value_distribution"] = self.query_value_distribution()
        
        print("\n[2/4] Analyzing server-level breakdown...")
        self.findings["server_breakdown"] = self.query_server_level_breakdown()
        
        print("\n[3/4] Checking threat intelligence coverage...")
        self.findings["threat_intel_analysis"] = self.query_threat_intel_coverage()
        
        print("\n[4/4] Checking detection logic health...")
        self.findings["detection_logic_health"] = self.query_detection_logic_health()
        
        print("\n" + "=" * 60)
        print("ROOT CAUSE ANALYSIS")
        print("=" * 60)
        self.findings["root_causes"] = self.analyze_root_causes()
        
        if not self.findings["root_causes"]:
            print("\n  No root causes identified - signal may be healthy")
        else:
            for cause in self.findings["root_causes"]:
                print(f"\n  [{cause['cause_id']}] {cause['cause_type'].upper()} ({cause['severity']})")
                print(f"      Evidence: {cause['evidence']}")
                print(f"      Details: {cause['details']}")
        
        print("\n" + "=" * 60)
        print("RECOMMENDATIONS")
        print("=" * 60)
        self.findings["recommendations"] = self.generate_recommendations()
        
        if not self.findings["recommendations"]:
            print("\n  No recommendations - investigation inconclusive")
        else:
            for i, rec in enumerate(self.findings["recommendations"], 1):
                print(f"  {i}. {rec}")
        
        # Summary
        dist = self.findings["value_distribution"]
        self.findings["investigation_summary"] = {
            "status": "COMPLETE",
            "distinct_values_found": len(dist.get("distinct_values", [])),
            "total_records": dist.get("total_records", 0),
            "primary_root_cause": self.findings["root_causes"][0]["cause_type"] if self.findings["root_causes"] else "NONE_IDENTIFIED",
            "severity": self.findings["root_causes"][0]["severity"] if self.findings["root_causes"] else "N/A",
            "recommendation_count": len(self.findings["recommendations"])
        }
        
        print("\n" + "=" * 60)
        print("INVESTIGATION SUMMARY")
        print("=" * 60)
        for key, val in self.findings["investigation_summary"].items():
            print(f"  {key}: {val}")
        print("=" * 60)
        
        return self.findings
    
    def save_report(self, output_path: str = "shared/outputs/goose/known_bad_pattern_investigation_report.json") -> None:
        """Save findings to JSON report."""
        import os
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.findings, f, indent=2, default=str)
        print(f"\nReport saved to: {output_path}")


def main():
    investigator = KnownBadPatternInvestigator()
    
    try:
        findings = investigator.run_investigation()
    except requests.exceptions.ConnectionError as e:
        log(f"ERROR: Could not connect to write_service: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"ERROR: Investigation failed: {e}")
        sys.exit(1)
    
    investigator.save_report()
    
    # Exit with code based on findings
    if findings["root_causes"]:
        exit(0)
    else:
        print("\nNo root causes identified - signal may be healthy")
        exit(0)


if __name__ == "__main__":
    main()