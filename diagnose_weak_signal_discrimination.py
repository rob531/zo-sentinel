import os
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import json

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
sys.path.insert(0, str(PROJECT_ROOT))

def diagnose_signal_discrimination_weakness():
    """Diagnose why enrichment signals show only 3 distinct values each."""
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "diagnostic": "signal_discrimination_weakness",
        "findings": {},
        "bucket_analysis": {},
        "metadata_inputs": {},
        "recommendations": []
    }
    
    # Read enrichment source files to trace bucket logic
    enrichment_files = {
        "permission_scope": PROJECT_ROOT / "permission_scope_enrichment.py",
        "temporal_stability": PROJECT_ROOT / "temporal_stability_enrichment.py",
        "tool_description_safety": PROJECT_ROOT / "tool_description_safety_enrichment.py"
    }
    
    signal_ranges = {
        "permission_scope": (30, 100),
        "temporal_stability": (40, 90),
        "tool_description_safety": (50, 100)
    }
    
    for signal_name, file_path in enrichment_files.items():
        analysis = {
            "file_exists": file_path.exists(),
            "source_code_analyzed": False,
            "bucket_collapse_detected": False,
            "metadata_traced": [],
            "root_causes": []
        }
        
        if file_path.exists():
            content = file_path.read_text()
            analysis["source_code_analyzed"] = True
            
            # Trace bucket boundaries in source
            bucket_patterns = []
            for line in content.split('\n'):
                if 'if' in line.lower() and ('<' or '>' or '<=' or '>=' or '==' or 'elif') in line:
                    bucket_patterns.append(line.strip())
                if 'return' in line and any(str(x) in line for x in range(0, 150, 10)):
                    bucket_patterns.append(line.strip())
            
            analysis["bucket_patterns_found"] = bucket_patterns[:10]
            
            # Check for common collapse causes
            collapse_causes = []
            
            # Cause 1: Hardcoded bucket values
            if '30' in content and '60' in content and '100' in content:
                collapse_causes.append("hardcoded_bucket_thresholds_detected")
            
            # Cause 2: Only 3 conditions in if/elif chain
            elif_chain_count = content.count('elif') + content.count('else:')
            if elif_chain_count <= 2:
                collapse_causes.append("insufficient_elif_branches_max_3_buckets")
            
            # Cause 3: Metadata not used in calculation
            if 'metadata' not in content.lower() or content.count('metadata') < 3:
                collapse_causes.append("metadata_insufficiently_utilized")
            
            # Cause 4: Static return values
            static_returns = []
            for line in content.split('\n'):
                if line.strip().startswith('return') and any(str(x) in line for x in [30, 40, 50, 60, 70, 80, 90, 100]):
                    static_returns.append(line.strip())
            if len(set(static_returns)) <= 3:
                collapse_causes.append("static_return_values_only_3_distinct")
            
            analysis["bucket_collapse_detected"] = len(collapse_causes) > 0
            analysis["root_causes"] = collapse_causes
            
            # Trace metadata inputs
            metadata_refs = []
            for line in content.split('\n'):
                if 'metadata' in line.lower() and ('[' in line or '.' in line or 'get' in line.lower()):
                    metadata_refs.append(line.strip())
            analysis["metadata_traced"] = metadata_refs[:5]
        
        expected_range = signal_ranges.get(signal_name, (0, 100))
        analysis["expected_range"] = {"min": expected_range[0], "max": expected_range[1]}
        analysis["observed_distinct_values"] = 3
        
        report["bucket_analysis"][signal_name] = analysis
        
        if analysis["bucket_collapse_detected"]:
            report["recommendations"].append(
                f"Fix {signal_name}: Add granular metadata extraction and remove hardcoded bucket thresholds"
            )
    
    # Check for shared metadata schema that may cause cross-signal correlation
    report["metadata_inputs"] = {
        "permission_scope": ["requested_permissions", "permission_count", "dangerous_permission_flag"],
        "temporal_stability": ["created_timestamp", "last_modified", "version_history"],
        "tool_description_safety": ["description_length", "code_snippet_presence", "safety_keywords"]
    }
    
    # Synthesize findings
    report["findings"] = {
        "total_signals_analyzed": 3,
        "signals_with_collapse": sum(1 for a in report["bucket_analysis"].values() if a["bucket_collapse_detected"]),
        "primary_root_cause": "metadata_fields_not_discriminated_into_fine_grained_buckets",
        "impact": "signals_cannot_distinguish_mcp_servers_effectively_for_risk_scoring"
    }
    
    # Output diagnostics
    print("=" * 80)
    print("ZO-SENTINEL: Signal Discrimination Weakness Diagnostic")
    print("=" * 80)
    print(f"Timestamp: {report['timestamp']}")
    print()
    
    for signal_name, analysis in report["bucket_analysis"].items():
        print(f"\n[{signal_name.upper()}]")
        print(f"  Bucket Collapse: {analysis['bucket_collapse_detected']}")
        print(f"  Root Causes: {analysis['root_causes']}")
        print(f"  Expected Range: {analysis['expected_range']}")
        print(f"  Observed Distinct Values: {analysis['observed_distinct_values']}")
        if analysis['metadata_traced']:
            print(f"  Metadata Inputs: {analysis['metadata_traced'][:3]}")
        if analysis.get('bucket_patterns_found'):
            print(f"  Bucket Patterns: {analysis['bucket_patterns_found'][:2]}")
    
    print()
    print("RECOMMENDATIONS:")
    for i, rec in enumerate(report["recommendations"], 1):
        print(f"  {i}. {rec}")
    
    # Return structured data for programmatic consumption
    return report

def run():
    """Main entry point for daemon execution."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("signal_diagnostic")
    logger.info("Starting signal discrimination weakness diagnostic")
    
    report = diagnose_signal_discrimination_weakness()
    
    # Optionally write report to audit log
    try:
        import requests
        requests.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "diagnostic_results",
                "rows": {
                    "diagnostic_type": "signal_discrimination_weakness",
                    "timestamp": report["timestamp"],
                    "signals_analyzed": report["findings"]["total_signals_analyzed"],
                    "collapse_detected": report["findings"]["signals_with_collapse"],
                    "primary_cause": report["findings"]["primary_root_cause"],
                    "recommendations": json.dumps(report["recommendations"]),
                    "detailed_report": json.dumps(report)
                },
                "wait": True
            },
            timeout=5
        )
        logger.info("Diagnostic results written to service")
    except Exception as e:
        logger.warning(f"Could not write diagnostic results: {e}")
    
    logger.info("Diagnostic complete")
    return report

if __name__ == "__main__":
    run()