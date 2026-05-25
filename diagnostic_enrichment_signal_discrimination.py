import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

from collections import defaultdict
import itertools

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

def ws_query(sql):
    import requests
    resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows):
    import requests
    resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=10)
    resp.raise_for_status()
    return resp.json()

def generate_synthetic_fingerprints():
    fingerprints = []
    
    registry_sources = ["npm", "github", "smithery", "anthropic", "pypi", "manual"]
    publisher_verifieds = [True, False]
    
    age_days_values = [1, 7, 30, 90, 180, 270, 365]
    download_count_values = [0, 10, 100, 1000, 5000, 10000]
    dependency_count_values = [0, 5, 10, 25, 50]
    stars_values = [0, 10, 100, 500, 1000, 2500, 5000]
    
    idx = 0
    for age in age_days_values:
        for dl in download_count_values:
            for dep in dependency_count_values:
                for stars in stars_values[:3]:
                    for pub in publisher_verifieds:
                        for reg in registry_sources[:2]:
                            if idx >= 34:
                                break
                            fingerprints.append({
                                "server_id": f"diag_{idx:03d}",
                                "registry_source": reg,
                                "age_days": age,
                                "download_count": dl,
                                "dependency_count": dep,
                                "publisher_verified": pub,
                                "stars": stars
                            })
                            idx += 1
                        if idx >= 34:
                            break
                    if idx >= 34:
                        break
                if idx >= 34:
                    break
            if idx >= 34:
                break
        if idx >= 34:
            break
    
    return fingerprints[:34]

def diagnose_module(module_name, compute_score_func, fingerprints):
    scores = []
    for fp in fingerprints:
        try:
            score = compute_score_func(fp)
            scores.append(float(score))
        except Exception as e:
            scores.append(None)
    
    valid_scores = [s for s in scores if s is not None]
    
    if not valid_scores:
        return {
            "module": module_name,
            "error": "No valid scores computed",
            "distinct_count": 0,
            "min": None,
            "max": None,
            "score_distribution": {}
        }
    
    distinct = sorted(set(valid_scores))
    distribution = defaultdict(int)
    for s in valid_scores:
        distribution[round(s, 4)] += 1
    
    return {
        "module": module_name,
        "distinct_count": len(distinct),
        "min": min(valid_scores),
        "max": max(valid_scores),
        "distinct_values": distinct,
        "score_distribution": dict(distribution),
        "total_fingerprints": len(fingerprints),
        "valid_scores": len(valid_scores)
    }

def diagnose_input_diversity(fingerprints):
    diversity_report = {}
    
    for key in ["age_days", "download_count", "dependency_count", "stars"]:
        values = [fp[key] for fp in fingerprints]
        diversity_report[key] = {
            "unique_values": len(set(values)),
            "min": min(values),
            "max": max(values),
            "sample": sorted(set(values))[:10]
        }
    
    reg_values = [fp["registry_source"] for fp in fingerprints]
    diversity_report["registry_source"] = {
        "unique_values": len(set(reg_values)),
        "values": list(set(reg_values))
    }
    
    pub_values = [fp["publisher_verified"] for fp in fingerprints]
    diversity_report["publisher_verified"] = {
        "unique_values": len(set(pub_values)),
        "values": list(set(pub_values))
    }
    
    return diversity_report

def run():
    print("=" * 70)
    print("ENRICHMENT SIGNAL DISCRIMINATION DIAGNOSTIC")
    print("=" * 70)
    
    findings = {
        "diagnostic_name": "enrichment_signal_discrimination",
        "target_modules": [
            "permission_scope_enrichment",
            "temporal_stability_enrichment", 
            "tool_description_safety_enrichment"
        ],
        "fingerprint_count": 34
    }
    
    print("\n[1] Generating 34 diverse synthetic fingerprints...")
    fingerprints = generate_synthetic_fingerprints()
    print(f"    Generated {len(fingerprints)} fingerprints")
    
    input_diversity = diagnose_input_diversity(fingerprints)
    findings["input_diversity_analysis"] = input_diversity
    print("\n[2] Input diversity analysis:")
    for key, info in input_diversity.items():
        print(f"    {key}: {info['unique_values']} unique values, range [{info.get('min', 'N/A')}, {info.get('max', 'N/A')}]")
    
    module_results = {}
    
    print("\n[3] Testing permission_scope_enrichment...")
    try:
        from permission_scope_enrichment import compute_score as psc_compute
        result = diagnose_module("permission_scope_enrichment", psc_compute, fingerprints)
        module_results["permission_scope_enrichment"] = result
        print(f"    Distinct scores: {result['distinct_count']}, Range: [{result['min']}, {result['max']}]")
        print(f"    Distribution: {result['score_distribution']}")
    except ImportError as e:
        module_results["permission_scope_enrichment"] = {"error": f"Import failed: {e}"}
        print(f"    ERROR: {e}")
    
    print("\n[4] Testing temporal_stability_enrichment...")
    try:
        from temporal_stability_enrichment import compute_score as ts_compute
        result = diagnose_module("temporal_stability_enrichment", ts_compute, fingerprints)
        module_results["temporal_stability_enrichment"] = result
        print(f"    Distinct scores: {result['distinct_count']}, Range: [{result['min']}, {result['max']}]")
        print(f"    Distribution: {result['score_distribution']}")
    except ImportError as e:
        module_results["temporal_stability_enrichment"] = {"error": f"Import failed: {e}"}
        print(f"    ERROR: {e}")
    
    print("\n[5] Testing tool_description_safety_enrichment...")
    try:
        from tool_description_safety_enrichment import compute_score as tds_compute
        result = diagnose_module("tool_description_safety_enrichment", tds_compute, fingerprints)
        module_results["tool_description_safety_enrichment"] = result
        print(f"    Distinct scores: {result['distinct_count']}, Range: [{result['min']}, {result['max']}]")
        print(f"    Distribution: {result['score_distribution']}")
    except ImportError as e:
        module_results["tool_description_safety_enrichment"] = {"error": f"Import failed: {e}"}
        print(f"    ERROR: {e}")
    
    findings["module_results"] = module_results
    
    print("\n[6] Root cause analysis...")
    weak_modules = []
    for mod_name, result in module_results.items():
        if isinstance(result, dict) and "distinct_count" in result:
            if result["distinct_count"] <= 3:
                weak_modules.append(mod_name)
    
    if weak_modules:
        print(f"    WEAK SIGNAL DETECTED in: {weak_modules}")
        
        input_diversity_ok = all(
            input_diversity.get(k, {}).get("unique_values", 0) > 1 
            for k in ["age_days", "download_count", "dependency_count", "stars", "registry_source", "publisher_verified"]
        )
        
        if input_diversity_ok:
            findings["root_cause"] = "formula_coarse_graining"
            findings["diagnosis"] = "Input diversity is sufficient, but scoring formulas produce coarse output. Likely due to: (1) sigmoid thresholds causing saturation, (2) bucketing/rounding in formulas, (3) conditional branches that map many inputs to same output."
            print("    Root cause: FORMULA COARSE-GRAINING (not input diversity)")
        else:
            findings["root_cause"] = "insufficient_input_diversity"
            findings["diagnosis"] = "Input fingerprints lack sufficient variation. Check fingerprint generation logic."
            print("    Root cause: INSUFFICIENT INPUT DIVERSITY")
    else:
        findings["root_cause"] = "none_detected"
        findings["diagnosis"] = "All modules produce sufficient distinct scores."
        print("    No weak signal detected")
    
    findings["recommendations"] = []
    if findings["root_cause"] == "formula_coarse_graining":
        findings["recommendations"].append("Review scoring formulas for hardcoded thresholds or bucket boundaries")
        findings["recommendations"].append("Consider using more granular sigmoid functions instead of step functions")
        findings["recommendations"].append("Add intermediate score bands between existing thresholds")
    
    print("\n" + "=" * 70)
    print("FINAL FINDINGS:")
    print("=" * 70)
    print(f"Root cause: {findings['root_cause']}")
    print(f"Diagnosis: {findings['diagnosis']}")
    print(f"Weak modules: {weak_modules}")
    print(f"Recommendations: {findings['recommendations']}")
    
    print("\n[7] Writing findings to service_health and audit_log...")
    try:
        ws_write("service_health", {
            "service": "diagnostic_enrichment_signal_discrimination",
            "last_heartbeat": "2025-01-01T00:00:00Z"
        })
        ws_write("audit_log", {
            "id": "diag_enrichment_signal_discrimination",
            "target_server_id": "diagnostic",
            "event_type": "signal_diagnostic",
            "actor": "system",
            "detail": str(findings)[:2000]
        })
    except Exception as e:
        print(f"    Warning: Could not write to service: {e}")
    
    return findings

if __name__ == '__main__':
    result = run()
    print("\nStructured output:")
    print(result)