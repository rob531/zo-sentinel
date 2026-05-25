import sys
import os
sys.path.insert(0, '/home/workspace/zo_sentinel')

import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

SERVICE_NAME = "verify_weak_signals_enrichment_effect"
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772")
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"

WEAK_SIGNALS = ["permission_scope", "temporal_stability", "tool_description_safety"]
DISCRIMINATIVE_THRESHOLD = 10

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"rows": [], "error": str(e)}

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def get_signal_scores(signal_name):
    sql = f"""
    SELECT server_id, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE signal_name = '{signal_name}'
    ORDER BY server_id, scored_at DESC
    """
    result = ws_query(sql)
    if "error" in result:
        return []
    
    rows = result.get("rows", [])
    latest_by_server = {}
    for row in rows:
        server_id = row.get("server_id")
        if server_id not in latest_by_server:
            latest_by_server[server_id] = row
    return list(latest_by_server.values())

def get_enrichment_records(signal_name):
    sql = f"""
    SELECT server_id, signal_name, score, evidence, computed_at
    FROM mcp_signal_enrichments
    WHERE signal_name = '{signal_name}'
    ORDER BY server_id, computed_at DESC
    """
    result = ws_query(sql)
    if "error" in result:
        return []
    return result.get("rows", [])

def compute_distinct_scores(records):
    if not records:
        return 0, 0.0, []
    scores = [r.get("score", 0) for r in records if r.get("score") is not None]
    if not scores:
        return 0, 0.0, []
    distinct = len(set(scores))
    if len(scores) > 1:
        spread = max(scores) - min(scores)
    else:
        spread = 0.0
    return distinct, spread, scores

def check_enricher_execution(signal_name):
    sql = f"""
    SELECT COUNT(*) as cnt, MAX(computed_at) as last_run
    FROM mcp_signal_enrichments
    WHERE signal_name = '{signal_name}'
    """
    result = ws_query(sql)
    if "error" in result or not result.get("rows"):
        return 0, None
    row = result["rows"][0]
    return row.get("cnt", 0), row.get("last_run")

def get_audit_log_for_signal(signal_name, limit=20):
    sql = f"""
    SELECT id, event_type, actor, detail, created_at
    FROM audit_log
    WHERE detail LIKE '%{signal_name}%'
    ORDER BY created_at DESC
    LIMIT {limit}
    """
    result = ws_query(sql)
    if "error" in result:
        return []
    return result.get("rows", [])

def analyze_scoring_logic(signal_name, records):
    issues = []
    
    if not records:
        issues.append("NO_RECORDS: No signal scores found in mcp_signal_scores")
        return issues
    
    scores = [r.get("score") for r in records if r.get("score") is not None]
    if not scores:
        issues.append("NULL_SCORES: All scores are NULL")
        return issues
    
    unique_scores = set(scores)
    
    if len(unique_scores) <= 3:
        issues.append(f"FLAT_SCORES: Only {len(unique_scores)} distinct values: {sorted(unique_scores)}")
    
    if len(unique_scores) == 1:
        score_val = list(unique_scores)[0]
        if score_val == 0:
            issues.append("CONSTANT_ZERO: All servers scored 0 - scoring logic may be broken")
        elif score_val == 1.0:
            issues.append("CONSTANT_ONE: All servers scored 1.0 - scoring logic may not be discriminative")
        else:
            issues.append(f"CONSTANT_VALUE: All servers scored {score_val}")
    
    evidence_counts = defaultdict(int)
    for r in records:
        ev = r.get("evidence", "")
        if ev:
            evidence_counts[type(ev).__name__] += 1
        else:
            evidence_counts["null"] += 1
    
    if evidence_counts.get("null", 0) > len(records) * 0.5:
        issues.append("NULL_EVIDENCE: More than 50% of records have null evidence")
    
    score_range = max(scores) - min(scores) if len(scores) > 1 else 0
    if score_range < 0.1:
        issues.append(f"NARROW_RANGE: Score range is {score_range:.3f}, expected >0.1 for discrimination")
    
    return issues

def diagnose_signal(signal_name):
    diagnostics = {
        "signal_name": signal_name,
        "signal_scores": {"total": 0, "distinct": 0, "spread": 0.0, "samples": []},
        "enrichments": {"total": 0, "distinct": 0, "last_run": None, "spread": 0.0},
        "enricher_execution": {"count": 0, "last_run": None, "recent_audit": []},
        "scoring_issues": [],
        "diagnosis": "UNKNOWN"
    }
    
    records = get_signal_scores(signal_name)
    diagnostics["signal_scores"]["total"] = len(records)
    distinct, spread, samples = compute_distinct_scores(records)
    diagnostics["signal_scores"]["distinct"] = distinct
    diagnostics["signal_scores"]["spread"] = spread
    if samples:
        diagnostics["signal_scores"]["samples"] = sorted(set(samples))[:20]
    
    enrich_records = get_enrichment_records(signal_name)
    if enrich_records:
        en_distinct, en_spread, _ = compute_distinct_scores(enrich_records)
        diagnostics["enrichments"]["total"] = len(enrich_records)
        diagnostics["enrichments"]["distinct"] = en_distinct
        diagnostics["enrichments"]["spread"] = en_spread
        if enrich_records:
            diagnostics["enrichments"]["last_run"] = enrich_records[0].get("computed_at")
    
    count, last_run = check_enricher_execution(signal_name)
    diagnostics["enricher_execution"]["count"] = count
    diagnostics["enricher_execution"]["last_run"] = last_run
    
    audit_records = get_audit_log_for_signal(signal_name, limit=10)
    diagnostics["enricher_execution"]["recent_audit"] = [
        {"event": r.get("event_type"), "actor": r.get("actor"), "at": r.get("created_at")}
        for r in audit_records
    ]
    
    diagnostics["scoring_issues"] = analyze_scoring_logic(signal_name, records)
    
    if distinct >= DISCRIMINATIVE_THRESHOLD:
        diagnostics["diagnosis"] = "DISCRIMINATIVE"
    elif distinct == 0:
        if count == 0:
            diagnostics["diagnosis"] = "ENRICHER_NOT_RUN: Enricher has never executed"
        else:
            diagnostics["diagnosis"] = "NO_SCORES_BUT_ENRICHER_RAN: Scores table empty despite enricher execution"
    elif distinct <= 3:
        if count == 0:
            diagnostics["diagnosis"] = "ENRICHER_NOT_RUN: Weak scores due to missing enricher execution"
        elif last_run:
            diagnostics["diagnosis"] = f"SCORING_LOGIC_GAP: Enricher ran but produces flat scores (last: {last_run})"
        else:
            diagnostics["diagnosis"] = "ENRICHER_NEVER_COMPLETED: Enricher table empty, never finished"
        if diagnostics["scoring_issues"]:
            diagnostics["diagnosis"] += f" | {', '.join(diagnostics['scoring_issues'][:2])}"
    else:
        diagnostics["diagnosis"] = f"PARTIALLY_DISCRIMINATIVE: {distinct} distinct values (threshold: {DISCRIMINATIVE_THRESHOLD})"
    
    return diagnostics

def print_report(all_diagnostics):
    print("\n" + "=" * 80)
    print("WEAK SIGNALS ENRICHMENT EFFECTIVENESS REPORT")
    print("=" * 80)
    print(f"Generated: {datetime.utcnow().isoformat()}")
    print(f"Discriminative Threshold: >= {DISCRIMINATIVE_THRESHOLD} distinct score values")
    print()
    
    summary = []
    for sig, diag in all_diagnostics.items():
        distinct = diag["signal_scores"]["distinct"]
        status = "DISCRIMINATIVE" if distinct >= DISCRIMINATIVE_THRESHOLD else "WEAK"
        summary.append({
            "signal": sig,
            "distinct": distinct,
            "total": diag["signal_scores"]["total"],
            "spread": diag["signal_scores"]["spread"],
            "enricher_count": diag["enricher_execution"]["count"],
            "enrichments_total": diag["enrichments"]["total"],
            "diagnosis": diag["diagnosis"],
            "status": status
        })
    
    print("-" * 80)
    print("SIGNAL SUMMARY")
    print("-" * 80)
    print(f"{'Signal':<25} {'Distinct':<10} {'Total':<8} {'Spread':<8} {'Enricher':<10} {'Diagnosis'[:40]}")
    print("-" * 80)
    for s in summary:
        print(f"{s['signal']:<25} {s['distinct']:<10} {s['total']:<8} {s['spread']:<8.3f} {s['enricher_count']:<10} {s['diagnosis'][:40]}")
    print()
    
    weak_signals = [s for s in summary if s["status"] == "WEAK"]
    if weak_signals:
        print("-" * 80)
        print("WEAK SIGNAL DIAGNOSIS")
        print("-" * 80)
        for s in weak_signals:
            diag = all_diagnostics[s["signal"]]
            print(f"\n[{s['signal']}]")
            print(f"  Status: WEAK (only {s['distinct']} distinct values)")
            print(f"  Diagnosis: {diag['diagnosis']}")
            print(f"  Signal Scores Table:")
            print(f"    - Total records: {s['total']}")
            print(f"    - Distinct scores: {s['distinct']}")
            print(f"    - Score range: {s['spread']:.4f}")
            if s['enricher_count'] > 0:
                print(f"  Enricher Execution:")
                print(f"    - Execution count: {s['enricher_count']}")
                print(f"    - Last run: {diag['enricher_execution']['last_run'] or 'UNKNOWN'}")
            else:
                print(f"  Enricher Execution: NEVER RAN")
            if diag["enrichments"]["total"] > 0:
                print(f"  Enrichments Table:")
                print(f"    - Total records: {diag['enrichments']['total']}")
                print(f"    - Distinct scores: {diag['enrichments']['distinct']}")
            if diag["scoring_issues"]:
                print(f"  Scoring Issues:")
                for issue in diag["scoring_issues"]:
                    print(f"    - {issue}")
            print()
    
    print("-" * 80)
    print("ROOT CAUSE ANALYSIS")
    print("-" * 80)
    
    enricher_not_run = all(s["enricher_count"] == 0 for s in summary if s["status"] == "WEAK")
    enricher_empty = all(diagnostics["enricher_execution"]["count"] == 0 for sig, diagnostics in all_diagnostics.items() if all_diagnostics[sig]["signal_scores"]["distinct"] < DISCRIMINATIVE_THRESHOLD)
    
    all_enrichers_empty = all(d["enricher_execution"]["count"] == 0 for d in all_diagnostics.values())
    
    if all_enrichers_empty:
        print("ROOT CAUSE: No enricher has ever executed")
        print("  - All three enrichers (permission_scope, temporal_stability, tool_description_safety)")
        print("    have zero records in mcp_signal_enrichments")
        print("  - The signal analyser's wiring to call these enrichers may be broken")
        print("  - The enricher modules may not be registered in the pipeline")
        print()
        print("RECOMMENDED ACTIONS:")
        print("  1. Check if enricher modules are registered in supervisord.conf")
        print("  2. Verify signal_analyser imports and calls the enricher compute_score()")
        print("  3. Check audit_log for any enricher-related events")
        print("  4. Verify trust_synthesiser_v3 wiring to consume enricher scores")
    else:
        signals_with_scores_but_flat = []
        for sig, diag in all_diagnostics.items():
            if diag["enricher_execution"]["count"] > 0 and diag["signal_scores"]["distinct"] <= 3:
                signals_with_scores_but_flat.append(sig)
        
        if signals_with_scores_but_flat:
            print("ROOT CAUSE: Enricher execution exists but scoring logic produces flat results")
            print(f"  - Affected signals: {', '.join(signals_with_scores_but_flat)}")
            print("  - These signals have enricher records but still produce <= 3 distinct values")
            print()
            print("RECOMMENDED ACTIONS:")
            print("  1. Review scoring algorithms in each enricher module")
            print("  2. Check if score computation uses server-specific features")
            print("  3. Verify evidence blob contains discriminative metadata")
            print("  4. Check if signal_analyser properly consumes enrichment scores")
        else:
            print("ROOT CAUSE: Partial enricher execution with mixed results")
            print("  - Some signals are discriminative, others are not")
            print("  - May indicate timing issues or selective execution")
            print()
            print("RECOMMENDED ACTIONS:")
            print("  1. Check logs for errors during enricher execution")
            print("  2. Verify all servers are being processed")
            print("  3. Check for race conditions in signal_analyser loop")
    
    print()
    print("-" * 80)
    print("VERDICT")
    print("-" * 80)
    
    all_discriminative = all(s["distinct"] >= DISCRIMINATIVE_THRESHOLD for s in summary)
    any_discriminative = any(s["distinct"] >= DISCRIMINATIVE_THRESHOLD for s in summary)
    none_have_scores = all(s["total"] == 0 for s in summary)
    
    if all_discriminative:
        print("ALL SIGNALS ARE DISCRIMINATIVE")
        print("  The signal enrichment pipeline is working correctly.")
        print(f"  All three signals have >= {DISCRIMINATIVE_THRESHOLD} distinct values.")
    elif none_have_scores:
        print("NO SIGNAL SCORES IN DATABASE")
        print("  The signal enrichment pipeline has never produced scores.")
        print("  Verify enricher modules are running and wired to signal_analyser.")
    elif not any_discriminative:
        print("ALL SIGNALS ARE WEAK")
        print("  Despite recent enricher builds, signals remain non-discriminative.")
        print("  Root cause analysis above identifies the specific issue.")
    else:
        partially_discriminative = [s for s in summary if s["distinct"] >= DISCRIMINATIVE_THRESHOLD]
        print(f"PARTIALLY DISCRIMINATIVE ({len(partially_discriminative)}/{len(summary)})")
        for s in partially_discriminative:
            print(f"  OK: {s['signal']} ({s['distinct']} distinct values)")
        weak = [s for s in summary if s["distinct"] < DISCRIMINATIVE_THRESHOLD]
        for s in weak:
            print(f"  WEAK: {s['signal']} ({s['distinct']} distinct values)")
    
    print()
    print("=" * 80)
    
    return all_discriminative

def run():
    all_diagnostics = {}
    
    for signal_name in WEAK_SIGNALS:
        diag = diagnose_signal(signal_name)
        all_diagnostics[signal_name] = diag
    
    is_discriminative = print_report(all_diagnostics)
    
    if is_discriminative:
        print("\nEXIT: 0 (All signals are discriminative)")
        sys.exit(0)
    else:
        print("\nEXIT: 1 (Weak signals detected - see diagnosis above)")
        sys.exit(1)

if __name__ == "__main__":
    run()