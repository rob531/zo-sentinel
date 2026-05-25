import os
import re
import ast

PROJECT_ROOT = "/home/workspace/zo_sentinel"

def check_trust_synthesiser_v2():
    source_path = os.path.join(PROJECT_ROOT, "trust_synthesiser_v2.py")
    companion_path = os.path.join(PROJECT_ROOT, "trust_synthesiser_injection_companion.py")
    
    with open(source_path, 'r') as f:
        source_content = f.read()
    
    errors = []
    warnings = []
    
    dimension_pattern = r"['\"]injection_resilience['\"]"
    if not re.search(dimension_pattern, source_content):
        errors.append("MISSING: dimension='injection_resilience' filter for mcp_signal_scores")
    
    weight_pattern = r"weight\s*[=:]\s*1\.6|1\.6\s*\*"
    if not re.search(weight_pattern, source_content):
        errors.append("MISSING: weight 1.6 for injection_resilience")
    
    threshold_pattern = r"threshold\s*[=:]\s*0\.80|0\.8\s*[<>=]"
    if not re.search(threshold_pattern, source_content):
        errors.append("MISSING: threshold 0.80 for injection_resilience")
    
    table_check = re.search(r"mcp_signal_scores", source_content)
    if not table_check:
        errors.append("MISSING: mcp_signal_scores table reference")
    
    if errors:
        companion_code = '''"""
Trust Synthesiser V2 - Injection Resilience Companion Module
Auto-generated fix for missing injection_resilience dimension handling
"""
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

INJECTION_RESILIENCE_WEIGHT = 1.6
INJECTION_RESILIENCE_THRESHOLD = 0.80
INJECTION_RESILIENCE_DIMENSION = "injection_resilience"

def get_injection_resilience_score(mcp_signal_scores_data: List[Dict[str, Any]]) -> Optional[float]:
    """
    Extract injection resilience score from MCP signal scores.
    
    Args:
        mcp_signal_scores_data: List of signal score records from mcp_signal_scores table
        
    Returns:
        Weighted injection resilience score or None if no data
    """
    injection_scores = [
        row.get('score', 0) 
        for row in mcp_signal_scores_data 
        if row.get('dimension') == INJECTION_RESILIENCE_DIMENSION
    ]
    
    if not injection_scores:
        logger.warning("No injection_resilience dimension found in mcp_signal_scores")
        return None
    
    avg_score = sum(injection_scores) / len(injection_scores)
    weighted_score = avg_score * INJECTION_RESILIENCE_WEIGHT
    
    logger.info(
        f"Injection resilience: avg={avg_score:.3f}, weighted={weighted_score:.3f} "
        f"(threshold={INJECTION_RESILIENCE_THRESHOLD})"
    )
    
    return weighted_score

def check_injection_resilience_threshold(weighted_score: float) -> bool:
    """
    Check if injection resilience score meets threshold.
    
    Args:
        weighted_score: The weighted injection resilience score
        
    Returns:
        True if score >= threshold, False otherwise
    """
    return weighted_score >= INJECTION_RESILIENCE_THRESHOLD

def enrich_trust_score(base_trust: float, mcp_signal_scores_data: List[Dict[str, Any]]) -> float:
    """
    Enrich base trust score with injection resilience dimension.
    
    Args:
        base_trust: Base trust score before injection consideration
        mcp_signal_scores_data: List of signal score records from mcp_signal_scores table
        
    Returns:
        Enriched trust score with injection resilience factor
    """
    injection_score = get_injection_resilience_score(mcp_signal_scores_data)
    
    if injection_score is None:
        return base_trust
    
    meets_threshold = check_injection_resilience_threshold(injection_score)
    
    if meets_threshold:
        adjustment = min(injection_score * 0.1, 0.05)
        enriched_trust = base_trust + adjustment
        logger.info(f"Trust enriched by +{adjustment:.4f} due to injection resilience")
    else:
        penalty = min((INJECTION_RESILIENCE_THRESHOLD - injection_score) * 0.1, 0.1)
        enriched_trust = max(base_trust - penalty, 0.0)
        logger.warning(f"Trust reduced by -{penalty:.4f} due to weak injection resilience")
    
    return enriched_trust
'''
        
        with open(companion_path, 'w') as f:
            f.write(companion_code)
        
        logger.warning(f"Companion module generated: {companion_path}")
        return {
            "status": "VALIDATION_FAILURE",
            "errors": errors,
            "companion_created": companion_path
        }
    
    return {
        "status": "VALIDATION_SUCCESS",
        "message": "trust_synthesiser_v2.py correctly implements injection_resilience handling"
    }

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    result = check_trust_synthesiser_v2()
    print(f"Validation Result: {result}")