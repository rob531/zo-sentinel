#!/usr/bin/env python3
"""
policy_engine_v2.py -- ZO-SENTINEL enhanced policy engine v2.
Evaluates MCP servers against policy rules and returns policy decisions.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
import requests
import hashlib
import logging

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8773/execute"

class DecisionType:
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"
    CONDITIONAL_ALLOW = "CONDITIONAL_ALLOW"
    ALLOW = "ALLOW"

VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED', 'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT', 'INSUFFICIENT']

TRUSTED_VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED']
CONDITIONAL_VERDICTS = ['CAUTION_LIMITED']
RISKY_VERDICTS = ['HIGH_RISK_ISOLATED', 'KNOWN_THREAT', 'INSUFFICIENT']

@dataclass
class PolicyDecision:
    decision: str
    conditions: List[str] = field(default_factory=list)
    rationale: str = ""
    policy_ids_matched: List[str] = field(default_factory=list)
    submission_id: str = ""

def ws_query(sql: str, params: Optional[List] = None) -> List[Dict]:
    """Execute SQL query against DuckDB via inference_router."""
    try:
        payload = {'sql': sql}
        if params:
            payload['params'] = params
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict) and 'rows' in result:
            return result['rows']
        return result if isinstance(result, list) else []
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: Any, wait: bool = True) -> Optional[Dict]:
    """Write rows to DuckDB via write_service."""
    try:
        payload = {'table': table, 'rows': rows, 'wait': wait}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Write failed: {e}")
        return None

def generate_submission_id(server_id: int, verdict: str) -> str:
    """Generate a unique submission ID for a policy decision."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    hash_input = f"{server_id}:{verdict}:{timestamp}"
    hash_val = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    return f"POL-{timestamp}-{hash_val}"

def load_active_policies() -> List[Dict]:
    """Load active policy rules from mcp_policy_rules table, ordered by priority."""
    sql = """
    SELECT id, rule_name, rule_type, pattern, action, description
    FROM mcp_policy_rules
    WHERE rule_type IS NOT NULL
    ORDER BY id ASC
    """
    return ws_query(sql)

def check_analyst_override(server_id: int) -> bool:
    """Check if an analyst has manually overridden for this server."""
    sql = """
    SELECT id FROM mcp_decisions
    WHERE server_id = ?
    AND decision IN ('ALLOW', 'CONDITIONAL_ALLOW')
    AND expires_at > NOW()
    ORDER BY decided_at DESC
    LIMIT 1
    """
    results = ws_query(sql, [str(server_id)])
    return len(results) > 0

def check_existing_pending(server_id: int) -> bool:
    """Check if there's already a pending decision for this server."""
    sql = """
    SELECT id FROM mcp_submissions
    WHERE mcp_identifier = ?
    AND status = 'pending'
    ORDER BY submitted_at DESC
    LIMIT 1
    """
    results = ws_query(sql, [str(server_id)])
    return len(results) > 0

def get_verdict_expiry_days(verdict: str) -> int:
    """Get expiry days based on verdict."""
    expiry_map = {
        'TRUSTED_GENERAL': 30,
        'TRUSTED_RESEARCH': 60,
        'ENTERPRISE_CONTROLLED': 90,
        'CAUTION_LIMITED': 120,
        'HIGH_RISK_ISOLATED': 180,
        'KNOWN_THREAT': 240,
        'INSUFFICIENT': 360
    }
    return expiry_map.get(verdict, 90)

def evaluate_policy(
    server_id: int,
    trust_score: float,
    verdict: str,
    context: Optional[Dict[str, Any]] = None
) -> PolicyDecision:
    """
    Evaluate policy for an MCP server based on trust score, verdict, and context.
    
    Args:
        server_id: The MCP server ID
        trust_score: Trust score (0-100)
        verdict: Trust verdict
        context: Optional dict with environment and data_sensitivity
    
    Returns:
        PolicyDecision with decision, conditions, rationale, and matched policy IDs
    """
    context = context or {}
    environment = context.get('environment', 'production')
    data_sensitivity = context.get('data_sensitivity', 'medium')
    matched_policies = []
    conditions = []
    rationale_parts = []
    
    has_override = check_analyst_override(server_id)
    has_pending = check_existing_pending(server_id)
    
    submission_id = generate_submission_id(server_id, verdict)
    
    if has_pending:
        return PolicyDecision(
            decision=DecisionType.ESCALATE,
            rationale="Pending decision already exists for this server - awaiting analyst review",
            policy_ids_matched=['EXISTING_PENDING'],
            submission_id=submission_id
        )
    
    policies = load_active_policies()
    
    verdict_upper = verdict.upper() if verdict else 'UNKNOWN'
    
    if verdict_upper == 'KNOWN_THREAT':
        return PolicyDecision(
            decision=DecisionType.BLOCK,
            rationale="Server flagged as KNOWN_THREAT - insufficient assurance for enterprise deployment",
            policy_ids_matched=['DEFAULT_KNOWN_THREAT'],
            submission_id=submission_id
        )
    
    if verdict_upper == 'HIGH_RISK_ISOLATED' and not has_override:
        return PolicyDecision(
            decision=DecisionType.ESCALATE,
            rationale="HIGH_RISK_ISOLATED verdict requires analyst review before determination",
            conditions=["Analyst override required for production deployment"],
            policy_ids_matched=['DEFAULT_HIGH_RISK_ESCALATE'],
            submission_id=submission_id
        )
    
    if trust_score < 35:
        return PolicyDecision(
            decision=DecisionType.ESCALATE,
            rationale=f"Trust score {trust_score} below minimum threshold (35) - requires additional review",
            conditions=["Trust score must be >= 35 or analyst approval required"],
            policy_ids_matched=['DEFAULT_TRUST_THRESHOLD'],
            submission_id=submission_id
        )
    
    for policy in policies:
        rule_type = policy.get('rule_type', '')
        pattern = policy.get('pattern', '')
        action = policy.get('action', '')
        rule_name = policy.get('rule_name', '')
        description = policy.get('description', '')
        policy_id = str(policy.get('id', ''))
        
        applies = False
        
        if rule_type == 'PERMISSION_BLOCK':
            if environment == 'production':
                block_patterns = pattern.lower().split('|') if pattern else []
                if any(p in verdict_upper.lower() or p in str(context).lower() for p in block_patterns):
                    applies = True
                    conditions.append(f"Permission block: {description}")
        
        elif rule_type == 'ENVIRONMENT_CONSTRAINT':
            if environment == 'production' and data_sensitivity == 'high':
                applies = True
                conditions.append(f"Environment constraint: {description}")
        
        elif rule_type == 'VERDICT_BLOCK':
            blocked_verdicts = [v.strip().upper() for v in pattern.split('|')] if pattern else []
            if verdict_upper in blocked_verdicts:
                applies = True
                conditions.append(f"Verdict block: {description}")
        
        elif rule_type == 'SCORE_THRESHOLD':
            try:
                threshold = float(pattern)
                if trust_score < threshold:
                    applies = True
                    conditions.append(f"Score threshold: {description}")
            except (ValueError, TypeError):
                pass
        
        elif rule_type == 'ENVIRONMENT_RESTRICTION':
            restricted_envs = [e.strip().lower() for e in pattern.split('|')] if pattern else []
            if environment.lower() in restricted_envs:
                applies = True
                conditions.append(f"Environment restriction: {description}")
        
        if applies:
            matched_policies.append(policy_id)
            rationale_parts.append(description)
            
            if action == 'BLOCK':
                return PolicyDecision(
                    decision=DecisionType.BLOCK,
                    conditions=conditions,
                    rationale=f"Blocked by policy: {', '.join(rationale_parts)}",
                    policy_ids_matched=matched_policies,
                    submission_id=submission_id
                )
            elif action == 'ESCALATE':
                return PolicyDecision(
                    decision=DecisionType.ESCALATE,
                    conditions=conditions,
                    rationale=f"Escalation required: {', '.join(rationale_parts)}",
                    policy_ids_matched=matched_policies,
                    submission_id=submission_id
                )
            elif action == 'CONDITIONAL':
                pass
    
    if verdict_upper in ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH']:
        return PolicyDecision(
            decision=DecisionType.ALLOW,
            rationale=f"Server has {verdict} verdict - likely safe for enterprise use under formal security controls",
            policy_ids_matched=matched_policies or ['DEFAULT_TRUSTED_VERDICT'],
            submission_id=submission_id
        )
    
    if verdict_upper == 'ENTERPRISE_CONTROLLED':
        return PolicyDecision(
            decision=DecisionType.CONDITIONAL_ALLOW,
            conditions=["Maintain documented security controls", "Periodic re-attestation required"],
            rationale="Conditional approval pending specific mitigations - enterpris controlled deployment",
            policy_ids_matched=matched_policies or ['DEFAULT_ENTERPRISE_CONTROLLED'],
            submission_id=submission_id
        )
    
    if verdict_upper == 'CAUTION_LIMITED':
        return PolicyDecision(
            decision=DecisionType.CONDITIONAL_ALLOW,
            conditions=["Restricted to non-production environments", "Enhanced monitoring required"],
            rationale="Conditional approval pending specific mitigations for limited deployment",
            policy_ids_matched=matched_policies or ['DEFAULT_CAUTION_LIMITED'],
            submission_id=submission_id
        )
    
    return PolicyDecision(
        decision=DecisionType.CONDITIONAL_ALLOW,
        conditions=conditions if conditions else ["Standard monitoring and logging required"],
        rationale=f"Conditional approval based on verdict={verdict}, score={trust_score}, environment={environment}",
        policy_ids_matched=matched_policies or ['DEFAULT_CONDITIONAL'],
        submission_id=submission_id
    )

def store_decision(
    server_id: int,
    decision: PolicyDecision,
    requester_name: str = "policy_engine",
    requester_team: str = "automated",
    business_purpose: str = "Automated policy evaluation",
    analyst_name: str = None,
    expiry_days: int = None
) -> Optional[Dict]:
    """Store policy decision to mcp_decisions and mcp_submissions tables."""
    if not decision.submission_id:
        decision.submission_id = generate_submission_id(server_id, "")
    
    if expiry_days is None:
        expiry_days = get_verdict_expiry_days("UNKNOWN")
    
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expiry_days)
    
    submission_row = {
        'submission_id': decision.submission_id,
        'mcp_identifier': str(server_id),
        'requester_name': requester_name,
        'requester_team': requester_team,
        'business_purpose': business_purpose,
        'environment': 'production',
        'submitted_at': now.isoformat(),
        'status': 'pending' if decision.decision in [DecisionType.ESCALATE, DecisionType.CONDITIONAL_ALLOW] else 'decided'
    }
    
    decision_row = {
        'submission_id': decision.submission_id,
        'analyst_name': analyst_name,
        'decision': decision.decision,
        'conditions': '; '.join(decision.conditions) if decision.conditions else None,
        'notes': decision.rationale,
        'expiry_days': expiry_days,
        'expires_at': expires_at.isoformat(),
        'decided_at': now.isoformat()
    }
    
    try:
        ws_write('mcp_submissions', submission_row)
        ws_write('mcp_decisions', decision_row)
        return {'submission_id': decision.submission_id, 'decision': decision.decision}
    except Exception as e:
        log.error(f"Failed to store decision: {e}")
        return None

def full_evaluation(
    server_id: int,
    trust_score: float,
    verdict: str,
    context: Optional[Dict[str, Any]] = None,
    store: bool = True
) -> PolicyDecision:
    """
    Full policy evaluation with optional storage.
    
    Args:
        server_id: The MCP server ID
        trust_score: Trust score (0-100)
        verdict: Trust verdict
        context: Optional dict with environment and data_sensitivity
        store: Whether to store decision to database
    
    Returns:
        PolicyDecision with full evaluation details
    """
    decision = evaluate_policy(server_id, trust_score, verdict, context)
    
    if store:
        store_decision(server_id, decision)
    
    return decision

if __name__ == '__main__':
    import json
    
    print("=== Policy Engine v2 Test Suite ===\n")
    
    test_cases = [
        (1, 25.0, 'KNOWN_THREAT', {}),
        (2, 40.0, 'HIGH_RISK_ISOLATED', {}),
        (3, 30.0, 'CAUTION_LIMITED', {}),
        (4, 85.0, 'TRUSTED_GENERAL', {}),
        (5, 60.0, 'ENTERPRISE_CONTROLLED', {'environment': 'production', 'data_sensitivity': 'high'}),
        (6, 55.0, 'TRUSTED_RESEARCH', {'environment': 'research'}),
        (7, 20.0, 'INSUFFICIENT', {}),
        (8, 90.0, 'TRUSTED_GENERAL', {'environment': 'staging', 'data_sensitivity': 'low'}),
    ]
    
    for i, (server_id, trust_score, verdict, context) in enumerate(test_cases, 1):
        print(f"Test {i}: Server {server_id}")
        print(f"  Verdict: {verdict}, Trust: {trust_score}")
        print(f"  Context: {context}")
        
        decision = full_evaluation(server_id, trust_score, verdict, context, store=False)
        
        print(f"  Decision: {decision.decision}")
        print(f"  Rationale: {decision.rationale}")
        if decision.conditions:
            print(f"  Conditions: {decision.conditions}")
        print(f"  Submission ID: {decision.submission_id}")
        print(f"  Policies Matched: {decision.policy_ids_matched}")
        print()
    
    print("=== Usage Example ===")
    print("""
from policy_engine_v2 import full_evaluation, DecisionType

decision = full_evaluation(
    server_id=123,
    trust_score=75.5,
    verdict='ENTERPRISE_CONTROLLED',
    context={'environment': 'production', 'data_sensitivity': 'high'},
    store=True
)

if decision.decision == DecisionType.BLOCK:
    print("Access blocked")
elif decision.decision == DecisionType.ESCALATE:
    print("Requires analyst review")
elif decision.decision == DecisionType.CONDITIONAL_ALLOW:
    print(f"Conditional - conditions: {decision.conditions}")
else:
    print("Access allowed")
""")