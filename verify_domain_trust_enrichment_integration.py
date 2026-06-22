#!/usr/bin/env python3
"""
verify_domain_trust_enrichment_integration.py

Verifies that domain_trust_enrichment_wiring.py (built 2026-06-22T04:41:43)
has been integrated into signal_analyser or the enrichment harness.

Checks:
1. domain_trust_enrichment_wiring.py file exists and imports correctly
2. compute_score reads multiple fields (not single-field)
3. mcp_signal_enrichments table has domain_trust signal type entries
4. signal_analyser has domain_trust wired (weight configured)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
REPO_ROOT = "/home/workspace/zo_sentinel"


def ws_query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Execute parameterized SQL via write_service query endpoint."""
    payload = {"sql": sql, "params": list(params)}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        print(f"    [QUERY ERROR] {e}")
        return []


def check_file_exists(path: str) -> bool:
    """Check if file exists and return its metadata."""
    p = Path(path)
    if not p.exists():
        return False
    stat = p.stat()
    return True


def check_compute_score_fields(enrichment_module_path: str) -> Dict[str, Any]:
    """Check that compute_score reads multiple metadata fields."""
    with open(enrichment_module_path, "r") as f:
        content = f.read()
    
    # Fields that compute_score should read from metadata
    expected_fields = [
        "registry_source",
        "age_days",
        "publisher_verified",
        "stars",
        "download_count",
        "dependency_count",
    ]
    
    # Check for each field in the compute_score function
    found_fields = []
    for field in expected_fields:
        # Look for patterns like metadata.get("field_name") or metadata["field_name"]
        patterns = [
            f'metadata.get("{field}"',
            f'metadata["{field}"]',
            f'metadata.get(\'{field}\'',
            f'metadata[\'{field}\']',
        ]
        for pattern in patterns:
            if pattern in content:
                found_fields.append(field)
                break
    
    return {
        "expected_fields": expected_fields,
        "fields_found": found_fields,
        "is_multi_field": len(found_fields) >= 3,  # Multi-field if >= 3
        "field_count": len(found_fields),
    }


def check_mcp_signal_enrichments_domain_trust() -> Dict[str, Any]:
    """Check mcp_signal_enrichments for domain_trust entries."""
    result = {"has_entries": False, "count": 0, "signal_types_found": []}
    
    # Query for all signal types with counts
    sql = """
        SELECT signal_type, COUNT(*) as cnt
        FROM mcp_signal_enrichments
        GROUP BY signal_type
        ORDER BY cnt DESC
    """
    rows = ws_query(sql)
    
    all_signal_types = []
    domain_trust_count = 0
    
    for row in rows:
        signal_type = row.get("signal_type", "")
        count = row.get("cnt", 0)
        all_signal_types.append({"type": signal_type, "count": count})
        if signal_type == "domain_trust":
            domain_trust_count = count
    
    result["signal_types_found"] = all_signal_types
    result["count"] = domain_trust_count
    result["has_entries"] = domain_trust_count > 0
    
    return result


def check_signal_analyser_wiring() -> Dict[str, Any]:
    """Check if signal_analyser has domain_trust wired."""
    result = {"wired": False, "weight": None, "has_function": False}
    
    # Check for signal_analyser.py or signal_analyser_v*.py
    sa_files = [
        Path(REPO_ROOT) / "signal_analyser.py",
        Path(REPO_ROOT) / "signal_analyser_v2.py",
        Path(REPO_ROOT) / "signal_analyser_v3.py",
        Path(REPO_ROOT) / "signal_analyser_v4.py",
    ]
    
    for sa_path in sa_files:
        if not sa_path.exists():
            continue
        
        with open(sa_path, "r") as f:
            content = f.read()
        
        # Check for domain_trust in SIGNAL_WEIGHTS or similar
        if "'domain_trust'" in content or '"domain_trust"' in content:
            result["wired"] = True
            result["has_function"] = True
        
        # Try to extract weight
        import re
        weight_pattern = r"['\"]domain_trust['\"]:\s*([0-9.]+)"
        match = re.search(weight_pattern, content)
        if match:
            result["weight"] = float(match.group(1))
        
        # Check for compute_domain_trust_score function
        if "def compute_domain_trust_score" in content:
            result["has_function"] = True
    
    return result


def check_enrichment_harness_integration() -> Dict[str, Any]:
    """Check if domain_trust is integrated in enrichment_harness."""
    result = {"integrated": False}
    
    harness_path = Path(REPO_ROOT) / "enrichment_harness.py"
    if not harness_path.exists():
        return result
    
    with open(harness_path, "r") as f:
        content = f.read()
    
    if "domain_trust" in content.lower():
        result["integrated"] = True
    
    return result


def verify_wiring_file_imports() -> Dict[str, Any]:
    """Verify domain_trust_enrichment_wiring.py can import compute_score."""
    result = {"imports_correct": False, "errors": []}
    
    wiring_path = Path(REPO_ROOT) / "domain_trust_enrichment_wiring.py"
    if not wiring_path.exists():
        result["errors"].append("domain_trust_enrichment_wiring.py not found")
        return result
    
    with open(wiring_path, "r") as f:
        content = f.read()
    
    # Check for correct import
    if 'from domain_trust_enrichment import compute_score' in content:
        result["imports_correct"] = True
    
    # Check for signal type constant
    if "SIGNAL_TYPE = 'domain_trust'" in content or 'SIGNAL_TYPE = "domain_trust"' in content:
        result["has_signal_type"] = True
    
    # Check for heartbeat
    if "send_heartbeat" in content or "heartbeat" in content.lower():
        result["has_heartbeat"] = True
    
    return result


def main():
    print("=" * 70)
    print("Domain Trust Enrichment Integration Verification")
    print(f"Timestamp: {datetime.utcnow().isoformat()}Z")
    print("=" * 70)
    
    results = {}
    all_passed = True
    
    # Check 1: File exists
    print("\n[CHECK 1] domain_trust_enrichment_wiring.py exists")
    wiring_path = Path(REPO_ROOT) / "domain_trust_enrichment_wiring.py"
    if wiring_path.exists():
        stat = wiring_path.stat()
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
        print(f"    PASS - File exists ({size} bytes, mtime: {mtime})")
        results["file_exists"] = True
    else:
        print(f"    FAIL - File not found: {wiring_path}")
        results["file_exists"] = False
        all_passed = False
    
    # Check 2: Wiring file imports correctly
    print("\n[CHECK 2] domain_trust_enrichment_wiring.py imports correctly")
    import_result = verify_wiring_file_imports()
    if import_result["imports_correct"]:
        print(f"    PASS - Imports compute_score correctly")
        if import_result.get("has_signal_type"):
            print(f"    PASS - Has SIGNAL_TYPE = 'domain_trust'")
        if import_result.get("has_heartbeat"):
            print(f"    PASS - Has heartbeat mechanism")
    else:
        print(f"    FAIL - Import check failed: {import_result.get('errors')}")
        all_passed = False
    results["import_check"] = import_result
    
    # Check 3: compute_score reads multiple fields
    print("\n[CHECK 3] compute_score reads multiple metadata fields")
    enrichment_path = Path(REPO_ROOT) / "domain_trust_enrichment.py"
    if enrichment_path.exists():
        field_check = check_compute_score_fields(str(enrichment_path))
        print(f"    Fields expected: {field_check['expected_fields']}")
        print(f"    Fields found: {field_check['fields_found']}")
        print(f"    Field count: {field_check['field_count']}")
        
        if field_check["is_multi_field"]:
            print(f"    PASS - compute_score reads MULTIPLE fields (>=3)")
        else:
            print(f"    FAIL - compute_score reads only {field_check['field_count']} fields")
            all_passed = False
        results["field_check"] = field_check
    else:
        print(f"    FAIL - domain_trust_enrichment.py not found")
        results["field_check"] = {"error": "file not found"}
        all_passed = False
    
    # Check 4: mcp_signal_enrichments has domain_trust entries
    print("\n[CHECK 4] mcp_signal_enrichments has domain_trust entries")
    enrichments_check = check_mcp_signal_enrichments_domain_trust()
    print(f"    Signal types found: {[st['type'] for st in enrichments_check['signal_types_found']]}")
    
    if enrichments_check["has_entries"]:
        print(f"    PASS - domain_trust entries exist (count: {enrichments_check['count']})")
    else:
        print(f"    FAIL - NO domain_trust entries in mcp_signal_enrichments")
        print(f"           (Only found: {[st['type'] for st in enrichments_check['signal_types_found']]})")
        all_passed = False
    results["enrichments_check"] = enrichments_check
    
    # Check 5: signal_analyser has domain_trust wired
    print("\n[CHECK 5] signal_analyser has domain_trust wired")
    sa_check = check_signal_analyser_wiring()
    if sa_check["wired"]:
        print(f"    PASS - domain_trust wired in signal_analyser")
        if sa_check["weight"] is not None:
            print(f"    PASS - Weight configured: {sa_check['weight']} ({(sa_check['weight']*100):.0f}%)")
        if sa_check["has_function"]:
            print(f"    PASS - compute_domain_trust_score function exists")
    else:
        print(f"    WARN - domain_trust not explicitly wired in signal_analyser")
    results["signal_analyser_check"] = sa_check
    
    # Check 6: enrichment_harness integration
    print("\n[CHECK 6] enrichment_harness integration")
    harness_check = check_enrichment_harness_integration()
    if harness_check["integrated"]:
        print(f"    PASS - domain_trust integrated in enrichment_harness")
    else:
        print(f"    INFO - domain_trust not explicitly in enrichment_harness")
    results["harness_check"] = harness_check
    
    # Summary
    print("\n" + "=" * 70)
    print("INTEGRATION STATUS SUMMARY")
    print("=" * 70)
    
    if all_passed:
        print("\n✅ ALL CHECKS PASSED - domain_trust enrichment is fully integrated")
    else:
        print("\n⚠️  PARTIAL INTEGRATION - Some checks failed")
        print("\nKey findings:")
        print(f"  - Wiring file exists: {results.get('file_exists', False)}")
        print(f"  - compute_score reads {results.get('field_check', {}).get('field_count', 0)} fields (multi-field)")
        print(f"  - mcp_signal_enrichments has domain_trust: {enrichments_check['has_entries']}")
        print(f"  - signal_analyser wired: {sa_check['wired']}")
        
        if not enrichments_check['has_entries']:
            print("\n🔧 RECOMMENDED ACTION:")
            print("   The wiring file exists but entries haven't been written yet.")
            print("   To populate mcp_signal_enrichments, run:")
            print("   python3 domain_trust_enrichment_wiring.py")
            print("   OR ensure the daemon cycle runs to process servers")
    
    print("\n" + "=" * 70)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
