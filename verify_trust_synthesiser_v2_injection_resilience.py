#!/usr/bin/env python3
"""
verify_trust_synthesiser_v2_injection_resilience.py
Verification module for Phase 8 quality pass.
Checks trust_synthesiser_v2.py includes injection_resilience dimension
with weight 1.6 and threshold 0.80.
"""

import sys
import time
import re
from pathlib import Path

sys.path.insert(0, '/home/workspace/zo_sentinel')

import requests
from fastapi import FastAPI
import uvicorn

SERVICE_NAME = "verify_trust_synthesiser_v2_injection_resilience"
PORT = 8786
WRITE_SERVICE = "http://127.0.0.1:8772"

app = FastAPI()

TRUST_SYNTHESIZER_PATH = Path("/home/workspace/zo_sentinel/trust_synthesiser_v2.py")

DIAGNOSTIC_BLOB = {
    "check": "injection_resilience_dimension",
    "status": None,
    "findings": {},
    "issues": [],
    "recommendations": []
}


def check_single_instance():
    """Ensure only one instance runs."""
    pid_file = Path(f"/tmp/{SERVICE_NAME}.pid")
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            import os
            os.kill(int(old_pid), 0)
            print(f"[FATAL] Service already running as PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    pid_file.write_text(str(__import__('os').getpid()))


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "service_health",
            "rows": {"service": SERVICE_NAME, "last_heartbeat": int(time.time())},
            "wait": True
        }, timeout=5)
    except Exception:
        pass


def query_db(sql: str) -> dict:
    """Query write_service."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": [], "count": 0}


def execute_db(sql: str) -> dict:
    """Execute on write_service."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/execute", json={"sql": sql}, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def read_synthesiser_source() -> str:
    """Read trust_synthesiser_v2.py source."""
    if not TRUST_SYNTHESIZER_PATH.exists():
        DIAGNOSTIC_BLOB["issues"].append(f"trust_synthesiser_v2.py not found at {TRUST_SYNTHESIZER_PATH}")
        return ""
    return TRUST_SYNTHESIZER_PATH.read_text()


def check_source_includes_injection_resilience(source: str) -> dict:
    """Check if source includes injection_resilience dimension."""
    findings = {
        "dimension_found": False,
        "weight_1_6_found": False,
        "threshold_0_80_found": False,
        "sql_query_pattern_found": False,
        "weight_assignment_line": None,
        "threshold_assignment_line": None,
        "query_line": None
    }

    if not source:
        return findings

    source_lower = source.lower()

    if "injection_resilience" in source_lower:
        findings["dimension_found"] = True

        for i, line in enumerate(source.split('\n'), 1):
            line_lower = line.lower()
            if "injection_resilience" in line_lower and "signal_scores" in line_lower:
                findings["query_line"] = {"line_num": i, "content": line.strip()}
                findings["sql_query_pattern_found"] = True

            if "injection_resilience" in line_lower and ("weight" in line_lower) and ("1.6" in line or "1.60" in line):
                findings["weight_1_6_found"] = True
                findings["weight_assignment_line"] = {"line_num": i, "content": line.strip()}

            if "injection_resilience" in line_lower and ("threshold" in line_lower) and ("0.80" in line or "0.8" in line):
                findings["threshold_0_80_found"] = True
                findings["threshold_assignment_line"] = {"line_num": i, "content": line.strip()}

    return findings


def get_injection_resilience_score_distribution() -> dict:
    """Query mcp_signal_scores for injection_resilience dimension."""
    result = {
        "dimension": "injection_resilience",
        "total_records": 0,
        "score_range": {"min": None, "max": None, "avg": None},
        "distribution_buckets": {},
        "servers_with_dimension": [],
        "error": None
    }

    query_result = query_db("""
        SELECT server_id, signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE signal_name = 'injection_resilience'
        ORDER BY score DESC
    """)

    if query_result.get("error"):
        result["error"] = query_result["error"]
        return result

    rows = query_result.get("rows", [])
    result["total_records"] = len(rows)

    if rows:
        scores = [r.get("score", 0) for r in rows if r.get("score") is not None]
        if scores:
            result["score_range"]["min"] = min(scores)
            result["score_range"]["max"] = max(scores)
            result["score_range"]["avg"] = round(sum(scores) / len(scores), 3)

        for bucket in ["0.00-0.20", "0.21-0.40", "0.41-0.60", "0.61-0.80", "0.81-1.00"]:
            result["distribution_buckets"][bucket] = 0

        for score in scores:
            if score <= 0.20:
                result["distribution_buckets"]["0.00-0.20"] += 1
            elif score <= 0.40:
                result["distribution_buckets"]["0.21-0.40"] += 1
            elif score <= 0.60:
                result["distribution_buckets"]["0.41-0.60"] += 1
            elif score <= 0.80:
                result["distribution_buckets"]["0.61-0.80"] += 1
            else:
                result["distribution_buckets"]["0.81-1.00"] += 1

        for row in rows:
            result["servers_with_dimension"].append({
                "server_id": row.get("server_id"),
                "score": row.get("score"),
                "evidence": str(row.get("evidence", ""))[:200]
            })

    return result


def check_composite_scoring_formula(source: str) -> dict:
    """Validate composite scoring formula includes injection_resilience."""
    result = {
        "composite_formula_found": False,
        "injection_resilience_in_formula": False,
        "formula_lines": [],
        "notes": []
    }

    if not source:
        return result

    formula_patterns = [
        r"(composite|total|final).*score",
        r"score\s*=\s*[^;]+",
        r"weighted.*score",
        r"trust.*score.*="
    ]

    composite_lines = []
    for i, line in enumerate(source.split('\n'), 1):
        line_lower = line.lower()
        for pattern in formula_patterns:
            if re.search(pattern, line_lower, re.IGNORECASE):
                composite_lines.append({"line_num": i, "content": line.strip()})
                break

    if composite_lines:
        result["composite_formula_found"] = True
        result["formula_lines"] = composite_lines

        full_formula = '\n'.join([f["content"] for f in composite_lines])
        if "injection_resilience" in full_formula.lower():
            result["injection_resilience_in_formula"] = True
        else:
            result["notes"].append("WARNING: injection_resilience not detected in composite formula lines")

    return result


def generate_verification_report(source_findings: dict, db_findings: dict, formula_findings: dict) -> dict:
    """Generate verification report with diagnostic blob."""
    report = {
        "verification_check": "trust_synthesiser_v2 injection_resilience dimension",
        "timestamp": int(time.time()),
        "source_analysis": {
            "path": str(TRUST_SYNTHESIZER_PATH),
            "exists": TRUST_SYNTHESIZER_PATH.exists(),
            "dimension_found": source_findings.get("dimension_found", False),
            "weight_1_6_found": source_findings.get("weight_1_6_found", False),
            "threshold_0_80_found": source_findings.get("threshold_0_80_found", False),
            "sql_query_pattern_found": source_findings.get("sql_query_pattern_found", False),
            "details": source_findings
        },
        "database_distribution": db_findings,
        "formula_validation": formula_findings,
        "overall_status": "PASS",
        "issues_found": [],
        "diagnostic_blob": {}
    }

    if not source_findings.get("dimension_found"):
        report["overall_status"] = "FAIL"
        report["issues_found"].append("CRITICAL: injection_resilience dimension not found in source code")
        DIAGNOSTIC_BLOB["issues"].append("Dimension 'injection_resilience' missing from trust_synthesiser_v2.py")

    if not source_findings.get("weight_1_6_found"):
        report["overall_status"] = "FAIL"
        report["issues_found"].append("CRITICAL: Weight 1.6 for injection_resilience not found in source code")
        DIAGNOSTIC_BLOB["issues"].append("Weight 1.6 NOT correctly assigned to injection_resilience dimension")

    if not source_findings.get("threshold_0_80_found"):
        report["overall_status"] = "FAIL"
        report["issues_found"].append("CRITICAL: Threshold 0.80 for injection_resilience not found in source code")
        DIAGNOSTIC_BLOB["issues"].append("Threshold 0.80 NOT correctly set for injection_resilience dimension")

    if not formula_findings.get("injection_resilience_in_formula"):
        report["issues_found"].append("WARNING: injection_resilience may not be included in composite formula")

    DIAGNOSTIC_BLOB["status"] = report["overall_status"]
    DIAGNOSTIC_BLOB["findings"] = {
        "source_weight_check": source_findings.get("weight_1_6_found", False),
        "source_threshold_check": source_findings.get("threshold_0_80_found", False),
        "db_records_found": db_findings.get("total_records", 0) > 0,
        "formula_includes_dimension": formula_findings.get("injection_resilience_in_formula", False)
    }

    if report["overall_status"] == "FAIL":
        DIAGNOSTIC_BLOB["recommendations"].append("Add 'injection_resilience' dimension reading from mcp_signal_scores table")
        DIAGNOSTIC_BLOB["recommendations"].append("Assign weight 1.6 to injection_resilience in the composite scoring")
        DIAGNOSTIC_BLOB["recommendations"].append("Set threshold 0.80 for injection_resilience dimension")
        DIAGNOSTIC_BLOB["recommendations"].append("Include injection_resilience in the final trust score calculation")

    report["diagnostic_blob"] = DIAGNOSTIC_BLOB

    return report


def run_verification():
    """Execute the verification checks."""
    print(f"[{SERVICE_NAME}] Starting verification...")

    source = read_synthesiser_source()
    source_findings = check_source_includes_injection_resilience(source)
    db_findings = get_injection_resilience_score_distribution()
    formula_findings = check_composite_scoring_formula(source)

    report = generate_verification_report(source_findings, db_findings, formula_findings)

    print(f"[{SERVICE_NAME}] Verification complete: {report['overall_status']}")
    print(f"[{SERVICE_NAME}] Issues found: {len(report['issues_found'])}")
    for issue in report['issues_found']:
        print(f"  - {issue}")

    if db_findings.get("total_records", 0) > 0:
        print(f"[{SERVICE_NAME}] DB injection_resilience records: {db_findings['total_records']}")
        print(f"[{SERVICE_NAME}] Score range: {db_findings['score_range']}")

    return report


@app.get("/health")
def health():
    """Health endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "uptime": int(time.time())}


@app.get("/verify")
def verify():
    """Run verification and return report."""
    report = run_verification()
    return report


@app.get("/diagnostic")
def diagnostic():
    """Return diagnostic blob."""
    return DIAGNOSTIC_BLOB


def run():
    """Main daemon loop."""
    check_single_instance()
    print(f"[{SERVICE_NAME}] Starting daemon on port {PORT}")

    report = run_verification()

    if report["overall_status"] == "PASS":
        print(f"[{SERVICE_NAME}] All checks PASSED")
    else:
        print(f"[{SERVICE_NAME}] Verification FAILED - see diagnostic for details")

    print(f"[{SERVICE_NAME}] Starting FastAPI server on port {PORT}")
    uvicorn.run(app, host='127.0.0.1', port=PORT)


if __name__ == '__main__':
    run()
else:
    run_verification()