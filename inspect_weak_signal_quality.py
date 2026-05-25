#!/usr/bin/env python3
"""
inspect_weak_signal_quality.py -- Inspect the three WEAK signals
(permission_scope, temporal_stability, tool_description_safety) which each
show only 3 distinct values across 34 fingerprints.

Runs enrichment_harness.py against v2 variants and reports distinct value counts.
"""

import argparse
import json
import subprocess
import sys
import os
from pathlib import Path
from collections import Counter


PROJECT_DIR = Path("/home/workspace/zo_sentinel")


def run_harness(enrichment_path: str, runs: int = 3, sample_size: int = 34) -> dict:
    """Run enrichment_harness.py and capture output."""
    cmd = [
        sys.executable,
        str(PROJECT_DIR / "enrichment_harness.py"),
        "--enrichment", enrichment_path,
        "--runs", str(runs),
        "--sample-size", str(sample_size),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def extract_distinct_values(output: str) -> list:
    """Extract distinct values from harness output JSON."""
    for line in output.split("\n"):
        if line.strip().startswith("{"):
            try:
                data = json.loads(line)
                return data.get("distinct_values", [])
            except json.JSONDecodeError:
                continue
    return []


def analyze_enrichment(enrichment_path: str, name: str) -> dict:
    """Analyze a single enrichment for distinct value distribution."""
    result = run_harness(str(enrichment_path))
    
    if result["returncode"] != 0:
        print(f"[!] {name} failed: {result['stderr'][:200]}")
        return {"name": name, "error": result["stderr"][:200], "distinct_count": 0, "distinct_values": []}
    
    output = result["stdout"]
    
    # Try to extract distinct values
    distinct_values = []
    
    # Look for JSON output with distinct_values
    for line in output.split("\n"):
        if "distinct" in line.lower() or "values" in line.lower():
            # Check if it's JSON
            if "{" in line:
                try:
                    # Find JSON object in line
                    start = line.find("{")
                    end = line.rfind("}") + 1
                    if start < end:
                        data = json.loads(line[start:end])
                        if "distinct_values" in data:
                            distinct_values = data["distinct_values"]
                        elif "values" in data:
                            distinct_values = data["values"]
                except json.JSONDecodeError:
                    pass
    
    # If no JSON, try to parse from text
    if not distinct_values and "distinct" in output.lower():
        # Extract number after "distinct"
        import re
        match = re.search(r"distinct[:\s]+(\d+)", output, re.IGNORECASE)
        if match:
            count = int(match.group(1))
            return {"name": name, "distinct_count": count, "distinct_values": [], "raw_output": output[:500]}
    
    distinct_count = len(distinct_values) if distinct_values else 0
    
    return {
        "name": name,
        "distinct_count": distinct_count,
        "distinct_values": distinct_values,
        "raw_output": output[:500] if output else result["stderr"][:500]
    }


def main():
    parser = argparse.ArgumentParser(description="Inspect weak signal enrichment quality")
    parser.add_argument("--runs", type=int, default=3, help="Number of harness runs per enrichment")
    parser.add_argument("--sample-size", type=int, default=34, help="Sample size for harness")
    args = parser.parse_args()
    
    print("=" * 70)
    print("ZO-SENTINEL: Weak Signal Enrichment Quality Inspector")
    print("=" * 70)
    print()
    
    # Define the weak signals and their v2 variants
    enrichments = [
        ("permission_scope_enrichment.py", "permission_scope (v2)"),
        ("temporal_stability_enrichment_v2.py", "temporal_stability (v2)"),
        ("tool_description_safety_enrichment_v2.py", "tool_description_safety (v2)"),
    ]
    
    results = []
    
    for filename, display_name in enrichments:
        enrichment_path = PROJECT_DIR / filename
        if not enrichment_path.exists():
            print(f"[!] {filename} not found, skipping")
            continue
        
        print(f"\n[>] Analyzing {display_name}...")
        result = analyze_enrichment(enrichment_path, display_name)
        results.append(result)
        
        if result.get("error"):
            print(f"    [ERROR] {result['error']}")
        else:
            print(f"    [OK] Distinct values: {result['distinct_count']}")
            if result.get("distinct_values"):
                print(f"    [VALUES] {result['distinct_values'][:10]}...")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Enrichment':<35} {'Distinct Values':>15}")
    print("-" * 70)
    
    for r in results:
        count = r.get("distinct_count", 0)
        flag = " [UPGRADE NEEDED]" if count <= 3 else " [OK]"
        print(f"{r['name']:<35} {count:>15}{flag}")
    
    print("-" * 70)
    
    # Check for improvements
    improved = [r for r in results if r.get("distinct_count", 0) > 3]
    if improved:
        print(f"\n[+] {len(improved)} enrichments show improvement over baseline (3 distinct values)")
    else:
        print("\n[!] All enrichments still at or below baseline (3 distinct values)")
        print("    Consider: more granular scoring, additional heuristics, or external data sources")


if __name__ == "__main__":
    main()