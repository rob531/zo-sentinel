import logging
import requests
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"

EXPECTED_WEIGHT = 1.6
EXPECTED_THRESHOLD = 0.80
DIMENSION = "injection_resilience"
SERVICE_NAME = "verify_trust_synthesiser_v2_dimension"

def query_db(sql):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {"rows": [], "count": 0}

def write_audit(event_type, detail):
    try:
        payload = {
            "table": "audit_log",
            "rows": {
                "event_type": event_type,
                "actor": SERVICE_NAME,
                "detail": json.dumps(detail),
                "created_at": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Write to audit_log failed: {e}")
        return {"ok": False, "error": str(e)}

def read_trust_synthesiser_source():
    try:
        with open("/home/workspace/zo_sentinel/trust_synthesiser_v2.py", "r") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Cannot read trust_synthesiser_v2.py: {e}")
        return None

def verify_dimension_implementation(source_code):
    findings = {
        "expected_weight": EXPECTED_WEIGHT,
        "expected_threshold": EXPECTED_THRESHOLD,
        "dimension": DIMENSION,
        "weight_found": False,
        "threshold_found": False,
        "weight_value": None,
        "threshold_value": None,
        "line_references": []
    }
    
    if not source_code:
        return findings
    
    lines = source_code.split('\n')
    for i, line in enumerate(lines, 1):
        line_lower = line.lower()
        if DIMENSION in line_lower:
            findings["line_references"].append({"line": i, "content": line.strip()})
        if "injection_resilience" in line_lower and "weight" in line_lower:
            findings["weight_found"] = True
            for part in line.split():
                try:
                    val = float(part.replace(",", "").replace(":", ""))
                    if 1.0 <= val <= 2.0:
                        findings["weight_value"] = val
                except:
                    pass
        if "injection_resilience" in line_lower and ("threshold" in line_lower or "0.80" in line or "0.8" in line):
            findings["threshold_found"] = True
            for part in line.split():
                try:
                    val = float(part.replace(",", "").replace(":", ""))
                    if 0.7 <= val <= 0.9:
                        findings["threshold_value"] = val
                except:
                    pass
    return findings

def verify_signal_scores_data():
    sql = f"""
    SELECT server_id, signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE signal_name = '{DIMENSION}'
    ORDER BY scored_at DESC
    LIMIT 100
    """
    result = query_db(sql)
    rows = result.get("rows", [])
    
    stats = {
        "total_rows": len(rows),
        "scored_servers": set(),
        "score_distribution": {"0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, "0.8-1.0": 0},
        "avg_score": 0.0,
        "weighted_scores_above_threshold": 0
    }
    
    if rows:
        scores = []
        for row in rows:
            if isinstance(row, dict):
                server_id = row.get("server_id", "")
                score = float(row.get("score", 0))
                scores.append(score)
                stats["scored_servers"].add(server_id)
                
                if score < 0.2:
                    stats["score_distribution"]["0-0.2"] += 1
                elif score < 0.4:
                    stats["score_distribution"]["0.2-0.4"] += 1
                elif score < 0.6:
                    stats["score_distribution"]["0.4-0.6"] += 1
                elif score < 0.8:
                    stats["score_distribution"]["0.6-0.8"] += 1
                else:
                    stats["score_distribution"]["0.8-1.0"] += 1
                
                weighted = score * EXPECTED_WEIGHT
                if weighted >= EXPECTED_THRESHOLD:
                    stats["weighted_scores_above_threshold"] += 1
        
        if scores:
            stats["avg_score"] = sum(scores) / len(scores)
    
    stats["scored_servers"] = len(stats["scored_servers"])
    return stats

def main():
    logger.info(f"Starting verification for dimension: {DIMENSION}")
    logger.info(f"Expected weight: {EXPECTED_WEIGHT}, Expected threshold: {EXPECTED_THRESHOLD}")
    
    source_findings = verify_dimension_implementation(read_trust_synthesiser_source())
    
    data_stats = verify_signal_scores_data()
    
    findings = {
        "verification_time": datetime.utcnow().isoformat(),
        "dimension": DIMENSION,
        "source_analysis": source_findings,
        "data_statistics": data_stats,
        "weight_correct": source_findings.get("weight_value") == EXPECTED_WEIGHT,
        "threshold_correct": source_findings.get("threshold_value") == EXPECTED_THRESHOLD,
        "recommendation": "PASS" if (
            source_findings.get("weight_value") == EXPECTED_WEIGHT and
            source_findings.get("threshold_value") == EXPECTED_THRESHOLD and
            data_stats["total_rows"] > 0
        ) else "REVIEW_REQUIRED"
    }
    
    logger.info(f"Verification complete: {json.dumps(findings, indent=2)}")
    
    write_audit("dimension_verification", findings)
    
    if findings["recommendation"] == "PASS":
        logger.info("VERIFICATION PASSED: trust_synthesiser_v2 applies correct weighting for injection_resilience")
    else:
        logger.warning("VERIFICATION FAILED: Review trust_synthesiser_v2.py implementation")
        if not source_findings["weight_found"]:
            logger.warning(f"  - Weight for {DIMENSION} not found in source")
        if not source_findings["threshold_found"]:
            logger.warning(f"  - Threshold {EXPECTED_THRESHOLD} for {DIMENSION} not found in source")
        if data_stats["total_rows"] == 0:
            logger.warning(f"  - No signal_scores data found for dimension {DIMENSION}")
    
    return findings

if __name__ == "__main__":
    main()