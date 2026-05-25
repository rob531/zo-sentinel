import logging
import json
import requests
from datetime import datetime, timezone
from typing import Dict, List, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

REQUIRED_SIGNAL_TYPES = [
    "domain_trust",
    "tool_description_safety",
    "permission_scope",
    "supply_chain",
    "community_signal",
    "temporal_stability",
    "supply_chain_enrichment",
    "community_signal_enrichment"
]

def verify_signal_enrichment_wiring() -> Dict[str, Any]:
    logger.info("Starting signal enrichment wiring verification")
    
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verification_status": "pending",
        "signal_types_found": [],
        "signal_types_missing": [],
        "enrichment_counts": {},
        "total_enrichments": 0,
        "wiring_issues": []
    }
    
    try:
        query_payload = {
            "table": "mcp_signal_enrichments",
            "query": "SELECT signal_type, COUNT(*) as count FROM mcp_signal_enrichments GROUP BY signal_type",
            "wait": True
        }
        
        logger.info(f"Querying mcp_signal_enrichments table for signal type distribution")
        response = requests.post(WRITE_SERVICE_URL, json=query_payload, timeout=30)
        response.raise_for_status()
        
        query_result = response.json()
        result["query_result"] = query_result
        
        if "rows" in query_result:
            for row in query_result["rows"]:
                signal_type = row.get("signal_type", "")
                count = row.get("count", 0)
                result["enrichment_counts"][signal_type] = count
                result["total_enrichments"] += count
                
                if signal_type in REQUIRED_SIGNAL_TYPES:
                    result["signal_types_found"].append(signal_type)
                elif any(sig in signal_type for sig in REQUIRED_SIGNAL_TYPES):
                    result["signal_types_found"].append(signal_type)
        
        result["signal_types_missing"] = [
            sig for sig in REQUIRED_SIGNAL_TYPES 
            if sig not in result["enrichment_counts"]
        ]
        
        if result["signal_types_missing"]:
            result["wiring_issues"].append(
                f"Missing signal types: {result['signal_types_missing']}"
            )
        
        if result["total_enrichments"] == 0:
            result["wiring_issues"].append("No enrichments found - signal analyser may not be writing to mcp_signal_enrichments table")
        
        result["verification_status"] = "passed" if not result["wiring_issues"] else "failed"
        
        logger.info(f"Verification complete: {result['verification_status']}")
        logger.info(f"Signal types found: {len(result['signal_types_found'])}/{len(REQUIRED_SIGNAL_TYPES)}")
        logger.info(f"Total enrichments: {result['total_enrichments']}")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to query mcp_signal_enrichments: {e}")
        result["verification_status"] = "error"
        result["wiring_issues"].append(f"Query failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during verification: {e}")
        result["verification_status"] = "error"
        result["wiring_issues"].append(f"Verification error: {str(e)}")
    
    return result

def check_score_flow() -> Dict[str, Any]:
    logger.info("Checking score flow to mcp_signal_enrichments")
    
    flow_check = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "score_columns_present": False,
        "average_scores": {},
        "score_coverage": {}
    }
    
    try:
        score_query = {
            "table": "mcp_signal_enrichments",
            "query": """
                SELECT signal_type, 
                       COUNT(*) as total_count,
                       SUM(CASE WHEN trust_score IS NOT NULL THEN 1 ELSE 0 END) as scored_count,
                       AVG(trust_score) as avg_score
                FROM mcp_signal_enrichments 
                GROUP BY signal_type
            """,
            "wait": True
        }
        
        response = requests.post(WRITE_SERVICE_URL, json=score_query, timeout=30)
        response.raise_for_status()
        
        score_result = response.json()
        
        if "rows" in score_result:
            flow_check["score_columns_present"] = True
            
            for row in score_result["rows"]:
                signal_type = row.get("signal_type", "unknown")
                total = row.get("total_count", 0)
                scored = row.get("scored_count", 0)
                avg = row.get("avg_score")
                
                coverage = (scored / total * 100) if total > 0 else 0
                flow_check["score_coverage"][signal_type] = {
                    "total": total,
                    "scored": scored,
                    "coverage_percent": round(coverage, 2)
                }
                
                if avg is not None:
                    flow_check["average_scores"][signal_type] = round(avg, 3)
        
        flow_check["status"] = "passed"
        logger.info(f"Score flow check complete. Coverage data: {flow_check['score_coverage']}")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to check score flow: {e}")
        flow_check["status"] = "error"
    except Exception as e:
        logger.error(f"Unexpected error checking score flow: {e}")
        flow_check["status"] = "error"
    
    return flow_check

def report_health():
    report = verify_signal_enrichment_wiring()
    score_flow = check_score_flow()
    
    full_report = {
        **report,
        "score_flow": score_flow
    }
    
    health_payload = {
        "table": "service_health",
        "rows": {
            "service": "signal_enrichment_wiring_check",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "status": full_report["verification_status"],
            "details": json.dumps({
                "signal_types_found": len(full_report["signal_types_found"]),
                "signal_types_missing": full_report["signal_types_missing"],
                "total_enrichments": full_report["total_enrichments"],
                "wiring_issues": full_report["wiring_issues"],
                "score_flow_status": score_flow.get("status", "unknown")
            })
        },
        "wait": True
    }
    
    try:
        requests.post(WRITE_SERVICE_URL, json=health_payload, timeout=10)
        logger.info("Health status reported to write_service")
    except Exception as e:
        logger.warning(f"Failed to report health: {e}")
    
    return full_report

def run():
    logger.info("=" * 60)
    logger.info("ZO-SENTINEL Signal Enrichment Wiring Check Starting")
    logger.info("=" * 60)
    
    wiring_result = verify_signal_enrichment_wiring()
    logger.info(f"Wiring verification result: {wiring_result['verification_status']}")
    
    score_flow = check_score_flow()
    logger.info(f"Score flow check result: {score_flow['status']}")
    
    report = report_health()
    
    logger.info("=" * 60)
    logger.info("Signal Enrichment Wiring Check Complete")
    logger.info(f"  - Signal types verified: {len(wiring_result['signal_types_found'])}/{len(REQUIRED_SIGNAL_TYPES)}")
    logger.info(f"  - Total enrichments in table: {wiring_result['total_enrichments']}")
    logger.info(f"  - Wiring issues: {len(wiring_result['wiring_issues'])}")
    logger.info("=" * 60)
    
    return report

if __name__ == '__main__':
    run()