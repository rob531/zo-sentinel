#!/usr/bin/env python3
"""
AIDR Commit Gateway Wiring - Verdict Check Enforcement
Phase 9 enterprise integration, priority 0.90
"""

import os
import sys
import logging
import logging.handlers
from datetime import datetime
from typing import Optional, Dict, Any, List
import requests

# Logging setup
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, 'aidr_gateway_wiring.log')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('aidr_gateway_wiring')
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Service endpoints
WRITE_SERVICE = "http://127.0.0.1:8772"
SEARCH_API = "http://127.0.0.1:8782"

# Verdict thresholds
CAUTION_LIMITED = "CAUTION_LIMITED"
HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
TRUSTED_VERDICTS = ["TRUSTED", "VERIFIED", "LOW_RISK", "MEDIUM_RISK"]

# Risk tier thresholds for injection resilience
MIN_INJECTION_RESILIENCE_SCORE = 50.0


def query_db(sql: str) -> Dict[str, Any]:
    """Query the database via write service."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": sql},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Database query failed: {e}")
        return {"rows": [], "count": 0}


def get_signal_score(server_id: str, signal_name: str = "injection_resilience") -> Optional[float]:
    """
    Query mcp_signal_scores for a specific signal dimension.
    Returns the score value or None if not found.
    """
    sql = f"""
        SELECT score 
        FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' 
        AND signal_name = '{signal_name}'
        ORDER BY scored_at DESC 
        LIMIT 1
    """
    result = query_db(sql)
    if result.get("rows") and len(result["rows"]) > 0:
        try:
            return float(result["rows"][0][0])
        except (ValueError, TypeError):
            pass
    return None


def get_server_verdict(server_id: str) -> Optional[str]:
    """
    Get the current verdict for a server from mcp_server_registry.
    """
    sql = f"""
        SELECT verdict 
        FROM mcp_server_registry 
        WHERE server_id = '{server_id}'
    """
    result = query_db(sql)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def get_risk_tier(server_id: str) -> Optional[str]:
    """
    Get the risk tier for a server from mcp_risk_register.
    """
    sql = f"""
        SELECT risk_tier 
        FROM mcp_risk_register 
        WHERE server_id = '{server_id}'
    """
    result = query_db(sql)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0][0]
    return None


def check_commit_allowed(
    server_id: str,
    override: bool = False,
    override_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Check if commit is allowed based on verdict and injection resilience.
    
    Returns:
        dict with keys: allowed (bool), verdict (str), injection_resilience (float),
                        reason (str), requires_approval (bool)
    """
    result = {
        "allowed": False,
        "verdict": None,
        "injection_resilience": None,
        "reason": "",
        "requires_approval": False
    }
    
    # Query verdict
    verdict = get_server_verdict(server_id)
    result["verdict"] = verdict
    
    if verdict is None:
        result["reason"] = "No verdict found for server"
        logger.warning(f"Server {server_id}: {result['reason']}")
        return result
    
    # Query injection resilience score
    resilience_score = get_signal_score(server_id)
    result["injection_resilience"] = resilience_score
    
    logger.info(f"Server {server_id}: verdict={verdict}, injection_resilience={resilience_score}")
    
    # Block for blocked verdicts
    blocked_verdicts = [CAUTION_LIMITED, HIGH_RISK_ISOLATED]
    if verdict in blocked_verdicts:
        if override:
            if not override_reason:
                result["reason"] = f"Override provided but no reason given for {verdict} verdict"
                logger.warning(f"Server {server_id}: {result['reason']}")
                return result
            result["requires_approval"] = True
            result["reason"] = f"Override approved: {override_reason}"
            result["allowed"] = True
            logger.warning(f"Server {server_id}: Override approved - {override_reason}")
            return result
        else:
            result["reason"] = f"Commit blocked: server has {verdict} verdict (no override)"
            logger.warning(f"Server {server_id}: {result['reason']}")
            return result
    
    # Check injection resilience threshold
    if resilience_score is not None and resilience_score < MIN_INJECTION_RESILIENCE_SCORE:
        if not override:
            result["reason"] = f"Commit blocked: injection_resilience score {resilience_score} below threshold {MIN_INJECTION_RESILIENCE_SCORE}"
            logger.warning(f"Server {server_id}: {result['reason']}")
            return result
        result["requires_approval"] = True
        result["reason"] = f"Override for low resilience: {override_reason or 'no reason'}"
        logger.warning(f"Server {server_id}: Low resilience override - {result['reason']}")
    
    # All checks passed
    result["allowed"] = True
    result["reason"] = f"Commit allowed: verdict={verdict}, resilience={resilience_score}"
    logger.info(f"Server {server_id}: {result['reason']}")
    return result


def build_commit_payload(
    server_id: str,
    include_resilience: bool = True,
    override: bool = False,
    override_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build the commit payload with verdict check enforcement.
    MUST include injection_resilience score in payload.
    """
    payload = {
        "server_id": server_id,
        "commit_timestamp": datetime.utcnow().isoformat(),
        "verdict_check_performed": True
    }
    
    # Query injection resilience and include in payload
    resilience_score = get_signal_score(server_id)
    if include_resilience:
        payload["injection_resilience"] = resilience_score
    
    # Get current verdict
    verdict = get_server_verdict(server_id)
    payload["current_verdict"] = verdict
    
    # Perform verdict check
    check_result = check_commit_allowed(server_id, override=override, override_reason=override_reason)
    payload["verdict_check_result"] = check_result
    
    if not check_result["allowed"]:
        payload["commit_allowed"] = False
        payload["block_reason"] = check_result["reason"]
        logger.info(f"Server {server_id}: Commit blocked - {check_result['reason']}")
        return payload
    
    payload["commit_allowed"] = True
    payload["requires_approval"] = check_result["requires_approval"]
    
    return payload


def execute_commit(
    server_id: str,
    commit_data: Dict[str, Any],
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Execute or simulate commit with verdict check enforcement.
    """
    if not commit_data.get("commit_allowed", False):
        return {
            "success": False,
            "reason": commit_data.get("block_reason", "Commit not allowed"),
            "commit_data": commit_data
        }
    
    if dry_run:
        logger.info(f"DRY RUN: Would commit server {server_id}")
        return {
            "success": True,
            "dry_run": True,
            "commit_data": commit_data
        }
    
    # Actual commit would go here - call to commit service
    logger.info(f"Executing commit for server {server_id}")
    return {
        "success": True,
        "dry_run": False,
        "commit_data": commit_data
    }


def validate_commit_request(
    server_id: str,
    override: bool = False,
    override_reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main entry point for commit validation.
    Queries mcp_signal_scores for injection_resilience dimension,
    blocks CAUTION_LIMITED/HIGH_RISK_ISOLATED unless explicit override.
    """
    logger.info(f"Validating commit request for server {server_id}, override={override}")
    
    # Step 1: Query injection resilience from mcp_signal_scores
    injection_resilience = get_signal_score(server_id)
    
    # Step 2: Get current verdict
    verdict = get_server_verdict(server_id)
    
    # Step 3: Perform verdict check (blocks CAUTION_LIMITED and HIGH_RISK_ISOLATED)
    verdict_check = check_commit_allowed(server_id, override=override, override_reason=override_reason)
    
    # Step 4: Build response
    response = {
        "server_id": server_id,
        "injection_resilience": injection_resilience,
        "verdict": verdict,
        "verdict_check": verdict_check,
        "can_commit": verdict_check["allowed"],
        "requires_override": not verdict_check["allowed"] and override,
        "requires_approval": verdict_check.get("requires_approval", False),
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if not verdict_check["allowed"]:
        response["block_reason"] = verdict_check["reason"]
        logger.warning(f"Commit validation failed for {server_id}: {verdict_check['reason']}")
    else:
        logger.info(f"Commit validated for {server_id}: can proceed")
    
    return response


# CLI interface for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AIDR Commit Gateway Verdict Check")
    parser.add_argument("server_id", help="Server ID to validate")
    parser.add_argument("--override", action="store_true", help="Override verdict blocks")
    parser.add_argument("--override-reason", help="Reason for override")
    parser.add_argument("--dry-run", action="store_true", help="Dry run only")
    
    args = parser.parse_args()
    
    result = validate_commit_request(
        args.server_id,
        override=args.override,
        override_reason=args.override_reason
    )
    
    print(f"Server: {result['server_id']}")
    print(f"Verdict: {result['verdict']}")
    print(f"Injection Resilience: {result['injection_resilience']}")
    print(f"Can Commit: {result['can_commit']}")
    print(f"Requires Approval: {result.get('requires_approval', False)}")
    
    if not result['can_commit']:
        print(f"Block Reason: {result.get('block_reason', 'Unknown')}")
    
    payload = build_commit_payload(
        args.server_id,
        include_resilience=True,
        override=args.override,
        override_reason=args.override_reason
    )
    print(f"\nCommit Payload: {payload}")