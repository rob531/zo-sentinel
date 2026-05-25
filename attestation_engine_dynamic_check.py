import os
import sys
import logging
import hashlib
from datetime import datetime, timezone

import requests

SERVICE_NAME = "attestation_engine_dynamic_check"
SERVICE_PORT = None
WRITE_SERVICE_URL = "http://localhost:8772"
PID_FILE = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    payload = {"sql": sql, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
    resp.raise_for_status()


def compute_deterministic_id(*fields) -> str:
    content = "|".join(str(f) for f in fields)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def read_attestation_engine_source() -> str:
    source_path = "/home/workspace/zo_sentinel/attestation_engine.py"
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Cannot find attestation_engine.py at {source_path}")
        return ""


def check_dynamic_evidence_in_attestations() -> dict:
    results = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_attestations": 0,
        "with_dynamic_scores": 0,
        "with_injection_resilience_ref": 0,
        "issues": []
    }
    
    query = """
        SELECT 
            server_id,
            server_name,
            attested_at,
            attestation_text,
            signal_dimensions
        FROM mcp_attestations
        ORDER BY attested_at DESC
        LIMIT 100
    """
    
    try:
        attestations = ws_query(query)
        results["total_attestations"] = len(attestations)
        
        if not attestations:
            logger.info("No attestations found in mcp_attestations table")
            return results
        
        logger.info(f"Checking {len(attestations)} attestations for dynamic evidence...")
        
        for att in attestations:
            att_text = att.get("attestation_text", "") or ""
            att_id = att.get("server_id", "unknown")
            
            dynamic_indicators = [
                "mcp_signal_scores",
                "signal_score",
                "computed_at",
                "confidence",
                "risk_level"
            ]
            
            has_dynamic = any(indicator in att_text.lower() for indicator in dynamic_indicators)
            
            if has_dynamic:
                results["with_dynamic_scores"] += 1
                logger.info(f"Attestation {att_id[:16]}... includes dynamic score references")
            else:
                results["issues"].append({
                    "type": "no_dynamic_evidence",
                    "server_id": att_id,
                    "message": "Attestation text does not reference dynamic signal scores"
                })
                logger.warning(f"Attestation {att_id[:16]}... missing dynamic score references")
            
            signal_dims = att.get("signal_dimensions", "") or ""
            if "injection_resilience" in signal_dims.lower() or "injection_resilience" in att_text.lower():
                results["with_injection_resilience_ref"] += 1
                logger.info(f"Attestation {att_id[:16]}... includes injection_resilience dimension")
        
    except Exception as e:
        logger.error(f"Error querying attestations: {e}")
        results["issues"].append({
            "type": "query_error",
            "message": str(e)
        })
    
    return results


def check_attestation_engine_uses_dynamic_evidence(source_code: str) -> dict:
    results = {
        "uses_signal_scores_table": False,
        "references_injection_resilience": False,
        "uses_template_only": False,
        "findings": []
    }
    
    if not source_code:
        results["findings"].append("attestation_engine.py source not found")
        return results
    
    source_lower = source_code.lower()
    
    if "mcp_signal_scores" in source_lower:
        results["uses_signal_scores_table"] = True
        results["findings"].append("attestation_engine.py references mcp_signal_scores table")
        logger.info("Source code references mcp_signal_scores")
    else:
        results["findings"].append("attestation_engine.py does NOT reference mcp_signal_scores")
        logger.warning("Source code missing mcp_signal_scores reference")
    
    if "injection_resilience" in source_lower:
        results["references_injection_resilience"] = True
        results["findings"].append("attestation_engine.py references injection_resilience dimension")
        logger.info("Source code references injection_resilience")
    
    static_template_indicators = [
        "static attestation",
        "hardcoded template",
        "no signal integration"
    ]
    
    for indicator in static_template_indicators:
        if indicator in source_lower:
            results["uses_template_only"] = True
            results["findings"].append(f"Possible static template pattern detected: {indicator}")
            logger.warning(f"Potential static template indicator: {indicator}")
    
    return results


def write_review_results(review_id: str, results: dict):
    now_iso = datetime.now(timezone.utc).isoformat()
    
    row = {
        "review_id": review_id,
        "review_type": "dynamic_evidence_check",
        "checked_at": now_iso,
        "total_attestations": results.get("total_attestations", 0),
        "with_dynamic_scores": results.get("with_dynamic_scores", 0),
        "with_injection_resilience": results.get("with_injection_resilience_ref", 0),
        "issues_count": len(results.get("issues", [])),
        "issues": str(results.get("issues", [])),
        "findings": str(results.get("findings", []))
    }
    
    try:
        ws_write("attestation_engine_dynamic_check", [row])
        logger.info(f"Wrote review results with id: {review_id}")
    except Exception as e:
        logger.error(f"Failed to write review results: {e}")


def run():
    logger.info("Starting attestation_engine dynamic evidence review...")
    
    review_id = compute_deterministic_id(
        "dynamic_evidence",
        datetime.now(timezone.utc).isoformat()
    )
    
    source_code = read_attestation_engine_source()
    
    source_results = check_attestation_engine_uses_dynamic_evidence(source_code)
    logger.info(f"Source code findings: {source_results['findings']}")
    
    attestation_results = check_dynamic_evidence_in_attestations()
    
    combined_results = {
        **source_results,
        **attestation_results
    }
    
    write_review_results(review_id, combined_results)
    
    logger.info("=" * 60)
    logger.info("DYNAMIC EVIDENCE REVIEW SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total attestations checked: {combined_results.get('total_attestations', 0)}")
    logger.info(f"With dynamic score references: {combined_results.get('with_dynamic_scores', 0)}")
    logger.info(f"With injection_resilience ref: {combined_results.get('with_injection_resilience_ref', 0)}")
    logger.info(f"Source uses mcp_signal_scores: {combined_results.get('uses_signal_scores_table', False)}")
    logger.info(f"Source references injection_resilience: {combined_results.get('references_injection_resilience', False)}")
    logger.info(f"Issues found: {len(combined_results.get('issues', []))}")
    for issue in combined_results.get("issues", []):
        logger.warning(f"  - {issue}")
    logger.info("=" * 60)
    
    if combined_results.get("uses_signal_scores_table") and combined_results.get("with_dynamic_scores", 0) > 0:
        logger.info("REVIEW PASSED: Attestation engine uses dynamic evidence appropriately")
    else:
        logger.warning("REVIEW FLAGGED: Attestation engine may be using static templates only")
    
    sys.exit(0)


if __name__ == "__main__":
    run()