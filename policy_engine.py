import requests
import time
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

SERVICE_NAME = 'policy_engine'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 30

RULE_TYPES = {
    'ENVIRONMENT_RESTRICTION': 'ENVIRONMENT_RESTRICTION',
    'PERMISSION_BLOCK': 'PERMISSION_BLOCK',
    'VENDOR_TRUST': 'VENDOR_TRUST',
    'SIGNAL_THRESHOLD': 'SIGNAL_THRESHOLD',
    'VERDICT_BLOCK': 'VERDICT_BLOCK'
}

POLICY_VERDICTS = {
    'BLOCK': 'BLOCK',
    'ESCALATE': 'ESCALATE',
    'ALLOW': 'ALLOW'
}

DANGEROUS_PERMISSIONS = {
    'shell', 'execute', 'exec', 'run_command', 'run_shell',
    'system', 'sudo', 'admin', 'root', 'write_file',
    'delete_file', 'rm', 'rmrf'
}

DANGEROUS_MCP_PATTERNS = [
    r'filesystem', r'file_', r'file-manager', r'file-manager',
    r'shell', r'cmd', r'bash', r'zsh', r'powershell',
    r'docker', r'container', r'kubectl', r'kubernetes',
    r'database', r'db-', r'sql-', r'mongo', r'postgres',
    r'ssh', r'remote', r'tunnel', r'vpn',
    r'keychain', r'credential', r'secret', r'password',
    r'process', r'kill', r'stop', r'restart'
]

KNOWN_THREAT_VERDICTS = {'DENIED', 'RUG_PULL_ALERT', 'PENDING_REVIEW'}


def ws_query(sql: str, params: Optional[List] = None) -> Any:
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: Any, wait: bool = True) -> Dict:
    """Write rows to DuckDB table via write_service."""
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat() -> None:
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def create_policy_rules_table() -> None:
    """Create mcp_policy_rules table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_policy_rules (
        id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        rule_type VARCHAR NOT NULL,
        description TEXT,
        enabled BOOLEAN DEFAULT TRUE,
        priority INTEGER DEFAULT 0,
        conditions JSON NOT NULL,
        action VARCHAR NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    try:
        ws_query(sql)
    except Exception as e:
        print(f"Table creation error (may already exist): {e}")


def load_active_policies() -> List[Dict]:
    """Load all enabled policy rules from DuckDB."""
    sql = """
    SELECT id, name, rule_type, description, conditions, action, priority
    FROM mcp_policy_rules
    WHERE enabled = TRUE
    ORDER BY priority DESC, created_at ASC
    """
    try:
        result = ws_query(sql)
        if isinstance(result, dict) and 'results' in result:
            return result.get('results', [])
        return result if isinstance(result, list) else []
    except Exception as e:
        print(f"Failed to load policies: {e}")
        return []


def evaluate_environment_restriction(
    conditions: Dict,
    submission: Dict,
    signals: Dict
) -> Optional[Dict]:
    """Evaluate ENVIRONMENT_RESTRICTION rule type.
    
    Conditions format:
    {
        "environment": "production|staging|development|all",
        "mcp_name_patterns": ["filesystem", "shell", ...],
        "match_mode": "any|all"
    }
    """
    target_env = conditions.get('environment', 'all').lower()
    mcp_patterns = conditions.get('mcp_name_patterns', [])
    match_mode = conditions.get('match_mode', 'any')
    
    submission_env = submission.get('environment', 'unknown').lower()
    
    if target_env != 'all' and submission_env != target_env:
        return None
    
    mcp_name = submission.get('mcp_name', '').lower()
    mcp_desc = submission.get('description', '').lower()
    mcp_tags = submission.get('tags', [])
    if isinstance(mcp_tags, list):
        mcp_tags_str = ' '.join(mcp_tags).lower()
    else:
        mcp_tags_str = str(mcp_tags).lower()
    
    combined_text = f"{mcp_name} {mcp_desc} {mcp_tags_str}"
    
    matches = []
    for pattern in mcp_patterns:
        pattern_lower = pattern.lower()
        if pattern_lower in combined_text or re.search(pattern_lower, combined_text):
            matches.append(pattern)
    
    if match_mode == 'any' and len(matches) > 0:
        return {
            'matched': True,
            'patterns': matches,
            'reason': f"MCP matched environment restriction: {submission_env} environment with patterns {matches}"
        }
    elif match_mode == 'all':
        if len(matches) == len(mcp_patterns):
            return {
                'matched': True,
                'patterns': matches,
                'reason': f"MCP matched all environment restriction patterns in {submission_env}"
            }
    
    return None


def evaluate_permission_block(
    conditions: Dict,
    submission: Dict,
    signals: Dict
) -> Optional[Dict]:
    """Evaluate PERMISSION_BLOCK rule type.
    
    Conditions format:
    {
        "permissions": ["shell", "execute", ...],
        "environment": "production|staging|all",
        "check_definition": true|false
    }
    """
    blocked_perms = conditions.get('permissions', [])
    target_env = conditions.get('environment', 'all').lower()
    check_definition = conditions.get('check_definition', True)
    
    submission_env = submission.get('environment', 'unknown').lower()
    
    if target_env != 'all' and submission_env != target_env:
        return None
    
    matched_perms = []
    
    if check_definition:
        definition = submission.get('definition', {})
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except:
                definition = {}
        
        tools = definition.get('tools', []) or definition.get('capabilities', []) or []
        for tool in tools:
            if isinstance(tool, dict):
                tool_name = tool.get('name', '').lower()
                tool_desc = tool.get('description', '').lower()
            else:
                tool_name = str(tool).lower()
                tool_desc = ''
            
            for perm in blocked_perms:
                perm_lower = perm.lower()
                if perm_lower in tool_name or perm_lower in tool_desc:
                    matched_perms.append(perm)
        
        if matched_perms:
            return {
                'matched': True,
                'permissions': list(set(matched_perms)),
                'reason': f"MCP requests blocked permissions: {set(matched_perms)} in {submission_env}"
            }
    
    for perm in blocked_perms:
        perm_lower = perm.lower()
        mcp_name = submission.get('mcp_name', '').lower()
        mcp_tags = submission.get('tags', [])
        if isinstance(mcp_tags, str):
            mcp_tags = [mcp_tags]
        
        for tag in mcp_tags:
            if perm_lower in tag.lower():
                matched_perms.append(perm)
                break
        
        if perm_lower in mcp_name:
            matched_perms.append(perm)
    
    if matched_perms:
        return {
            'matched': True,
            'permissions': list(set(matched_perms)),
            'reason': f"MCP has suspicious patterns for blocked permissions: {set(matched_perms)}"
        }
    
    return None


def evaluate_vendor_trust(
    conditions: Dict,
    submission: Dict,
    signals: Dict
) -> Optional[Dict]:
    """Evaluate VENDOR_TRUST rule type.
    
    Conditions format:
    {
        "trusted_vendors": ["github", "aws", ...],
        "match_field": "vendor|source|author|url",
        "auto_approve": true|false
    }
    """
    trusted_vendors = conditions.get('trusted_vendors', [])
    match_field = conditions.get('match_field', 'source')
    auto_approve = conditions.get('auto_approve', False)
    
    if not trusted_vendors:
        return None
    
    vendor_value = submission.get(match_field, '').lower()
    if not vendor_value:
        vendor_value = submission.get('author', '').lower()
        vendor_value = submission.get('source', '').lower()
    
    matched_vendor = None
    for vendor in trusted_vendors:
        if vendor.lower() in vendor_value:
            matched_vendor = vendor
            break
    
    if matched_vendor:
        return {
            'matched': True,
            'vendor': matched_vendor,
            'auto_approve': auto_approve,
            'reason': f"MCP from trusted vendor: {matched_vendor}"
        }
    
    return None


def evaluate_signal_threshold(
    conditions: Dict,
    submission: Dict,
    signals: Dict
) -> Optional[Dict]:
    """Evaluate SIGNAL_THRESHOLD rule type.
    
    Conditions format:
    {
        "signal_name": "trust_score|signal_score|...",
        "operator": "lt|gt|lte|gte|eq",
        "threshold": 30,
        "environment": "production|staging|all"
    }
    """
    signal_name = conditions.get('signal_name', 'trust_score')
    operator = conditions.get('operator', 'lt')
    threshold = conditions.get('threshold', 0)
    target_env = conditions.get('environment', 'all')
    
    submission_env = submission.get('environment', 'unknown').lower()
    
    if target_env != 'all' and submission_env != target_env:
        return None
    
    if signal_name == 'trust_score':
        value = submission.get('trust_score', signals.get('trust_score', 0))
    elif signal_name == 'signal_score':
        value = signals.get('signal_score', signals.get('overall_score', 0))
    else:
        value = signals.get(signal_name, 0)
    
    violated = False
    if operator == 'lt' and value < threshold:
        violated = True
    elif operator == 'lte' and value <= threshold:
        violated = True
    elif operator == 'gt' and value > threshold:
        violated = True
    elif operator == 'gte' and value >= threshold:
        violated = True
    elif operator == 'eq' and value == threshold:
        violated = True
    
    if violated:
        return {
            'matched': True,
            'signal': signal_name,
            'value': value,
            'threshold': threshold,
            'operator': operator,
            'reason': f"Signal threshold violated: {signal_name}={value} {operator} {threshold}"
        }
    
    return None


def evaluate_verdict_block(
    conditions: Dict,
    submission: Dict,
    verdict: str,
    signals: Dict
) -> Optional[Dict]:
    """Evaluate VERDICT_BLOCK rule type.
    
    Conditions format:
    {
        "blocked_verdicts": ["DENIED", "RUG_PULL_ALERT", ...],
        "environment": "production|staging|all"
    }
    """
    blocked_verdicts = conditions.get('blocked_verdicts', [])
    target_env = conditions.get('environment', 'all')
    
    submission_env = submission.get('environment', 'unknown').lower()
    
    if target_env != 'all' and submission_env != target_env:
        return None
    
    verdict_upper = verdict.upper()
    
    if verdict_upper in [v.upper() for v in blocked_verdicts]:
        matched_verdict = next((v for v in blocked_verdicts if v.upper() == verdict_upper), verdict_upper)
        return {
            'matched': True,
            'verdict': matched_verdict,
            'reason': f"MCP verdict {verdict_upper} is on blocked list"
        }
    
    return None


def evaluate_policy(
    submission_dict: Dict,
    trust_score: float,
    verdict: str,
    signals: Dict
) -> Dict[str, Any]:
    """Evaluate a submission against all active policy rules.
    
    Args:
        submission_dict: MCP server submission data
        trust_score: trust score from signal analysis
        verdict: verdict from approval workflow
        signals: dictionary of signal scores
    
    Returns:
        Dict with policy_verdict, triggered_rules, escalation_reason
    """
    policies = load_active_policies()
    
    triggered_rules = []
    escalation_reasons = []
    block_overrides = []
    auto_approve = False
    
    for policy in policies:
        rule_type = policy.get('rule_type', '')
        conditions = policy.get('conditions', {})
        action = policy.get('action', 'ESCALATE')
        rule_name = policy.get('name', 'Unknown Rule')
        
        evaluation = None
        
        if rule_type == 'ENVIRONMENT_RESTRICTION':
            evaluation = evaluate_environment_restriction(conditions, submission_dict, signals)
        elif rule_type == 'PERMISSION_BLOCK':
            evaluation = evaluate_permission_block(conditions, submission_dict, signals)
        elif rule_type == 'VENDOR_TRUST':
            evaluation = evaluate_vendor_trust(conditions, submission_dict, signals)
        elif rule_type == 'SIGNAL_THRESHOLD':
            evaluation = evaluate_signal_threshold(conditions, submission_dict, signals)
        elif rule_type == 'VERDICT_BLOCK':
            evaluation = evaluate_verdict_block(conditions, submission_dict, verdict, signals)
        
        if evaluation and evaluation.get('matched'):
            triggered_rules.append({
                'rule_id': policy.get('id', ''),
                'rule_name': rule_name,
                'rule_type': rule_type,
                'action': action,
                'evaluation': evaluation
            })
            
            if action == 'BLOCK':
                block_overrides.append(rule_name)
                escalation_reasons.append(evaluation.get('reason', f'Rule {rule_name} triggered'))
            elif action == 'ESCALATE':
                escalation_reasons.append(evaluation.get('reason', f'Rule {rule_name} triggered'))
            elif action == 'ALLOW' and evaluation.get('auto_approve'):
                auto_approve = True
    
    if block_overrides:
        return {
            'policy_verdict': 'BLOCK',
            'triggered_rules': triggered_rules,
            'escalation_reason': f"Blocked by policy rules: {', '.join(block_overrides)}"
        }
    
    if auto_approve:
        return {
            'policy_verdict': 'ALLOW',
            'triggered_rules': triggered_rules,
            'escalation_reason': 'Auto-approved by trusted vendor policy'
        }
    
    if escalation_reasons:
        return {
            'policy_verdict': 'ESCALATE',
            'triggered_rules': triggered_rules,
            'escalation_reason': '; '.join(escalation_reasons)
        }
    
    return {
        'policy_verdict': 'ALLOW',
        'triggered_rules': [],
        'escalation_reason': 'No policy rules triggered'
    }


def seed_default_policies() -> None:
    """Seed default policy rules into mcp_policy_rules table."""
    default_policies = [
        {
            'id': 'block-shell-prod',
            'name': 'Block Shell/Execute in Production',
            'rule_type': 'PERMISSION_BLOCK',
            'description': 'Block MCPs requesting shell/execute permissions in production environment',
            'enabled': True,
            'priority': 100,
            'conditions': {
                'permissions': list(DANGEROUS_PERMISSIONS),
                'environment': 'production',
                'check_definition': True
            },
            'action': 'BLOCK'
        },
        {
            'id': 'block-shell-all',
            'name': 'Block Shell/Execute Globally',
            'rule_type': 'PERMISSION_BLOCK',
            'description': 'Block MCPs requesting shell/execute permissions in any environment',
            'enabled': True,
            'priority': 90,
            'conditions': {
                'permissions': ['shell', 'execute', 'run_command', 'run_shell'],
                'environment': 'all',
                'check_definition': True
            },
            'action': 'BLOCK'
        },
        {
            'id': 'escalate-low-trust',
            'name': 'Escalate Low Trust Score',
            'rule_type': 'SIGNAL_THRESHOLD',
            'description': 'Require senior review for MCPs with trust_score below threshold',
            'enabled': True,
            'priority': 80,
            'conditions': {
                'signal_name': 'trust_score',
                'operator': 'lt',
                'threshold': 45,
                'environment': 'all'
            },
            'action': 'ESCALATE'
        },
        {
            'id': 'block-known-threat',
            'name': 'Block Known Threat Verdicts',
            'rule_type': 'VERDICT_BLOCK',
            'description': 'Block MCPs with KNOWN_THREAT verdict types',
            'enabled': True,
            'priority': 95,
            'conditions': {
                'blocked_verdicts': list(KNOWN_THREAT_VERDICTS),
                'environment': 'all'
            },
            'action': 'BLOCK'
        },
        {
            'id': 'escalate-filesystem-prod',
            'name': 'Escalate Filesystem MCPs in Production',
            'rule_type': 'ENVIRONMENT_RESTRICTION',
            'description': 'Require senior review for filesystem-type MCPs in production',
            'enabled': True,
            'priority': 70,
            'conditions': {
                'environment': 'production',
                'mcp_name_patterns': ['filesystem', 'file-manager', 'file-manager', 's3', 'storage'],
                'match_mode': 'any'
            },
            'action': 'ESCALATE'
        },
        {
            'id': 'escalate-container-prod',
            'name': 'Escalate Container MCPs in Production',
            'rule_type': 'ENVIRONMENT_RESTRICTION',
            'description': 'Require senior review for container orchestration MCPs in production',
            'enabled': True,
            'priority': 65,
            'conditions': {
                'environment': 'production',
                'mcp_name_patterns': ['docker', 'container', 'kubernetes', 'kubectl', 'k8s', 'helm'],
                'match_mode': 'any'
            },
            'action': 'ESCALATE'
        },
        {
            'id': 'escalate-database-prod',
            'name': 'Escalate Database MCPs in Production',
            'rule_type': 'ENVIRONMENT_RESTRICTION',
            'description': 'Require senior review for database access MCPs in production',
            'enabled': True,
            'priority': 60,
            'conditions': {
                'environment': 'production',
                'mcp_name_patterns': ['database', 'db', 'sql', 'postgres', 'mysql', 'mongo', 'redis', 'dynamodb'],
                'match_mode': 'any'
            },
            'action': 'ESCALATE'
        },
        {
            'id': 'escalate-credential-mcp',
            'name': 'Escalate Credential-Access MCPs',
            'rule_type': 'PERMISSION_BLOCK',
            'description': 'Require senior review for MCPs that appear to access credentials/secrets',
            'enabled': True,
            'priority': 75,
            'conditions': {
                'permissions': ['keychain', 'credential', 'secret', 'password', 'aws-access-key', 'api-key'],
                'environment': 'all',
                'check_definition': True
            },
            'action': 'ESCALATE'
        },
        {
            'id': 'block-rug-pull',
            'name': 'Block RUG_PULL_ALERT Verdicts',
            'rule_type': 'VERDICT_BLOCK',
            'description': 'Block MCPs that have triggered rug pull alerts - require full re-review',
            'enabled': True,
            'priority': 99,
            'conditions': {
                'blocked_verdicts': ['RUG_PULL_ALERT'],
                'environment': 'all'
            },
            'action': 'BLOCK'
        }
    ]
    
    for policy in default_policies:
        policy['created_at'] = datetime.now(timezone.utc).isoformat()
        policy['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    try:
        ws_write('mcp_policy_rules', default_policies, wait=True)
        print(f"Seeded {len(default_policies)} default policy rules")
    except Exception as e:
        print(f"Failed to seed policies: {e}")
        raise


def check_single_instance() -> None:
    """Ensure only one instance of daemon runs."""
    import os
    import signal
    import sys
    
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup():
        if os.path.exists(pid_file):
            os.remove(pid_file)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def cycle() -> None:
    """Main work cycle - refresh policies and validate existing ones."""
    try:
        policies = load_active_policies()
        print(f"Loaded {len(policies)} active policy rules")
        
        sql = """
        SELECT COUNT(*) as total FROM mcp_policy_rules WHERE enabled = TRUE
        """
        result = ws_query(sql)
        if isinstance(result, dict) and 'results' in result and len(result['results']) > 0:
            print(f"Policy engine status: {result['results'][0]['total']} active rules")
        elif isinstance(result, list) and len(result) > 0:
            print(f"Policy engine status: {result[0].get('total', 0)} active rules")
        
    except Exception as e:
        print(f"Error in policy cycle: {e}")


def run() -> None:
    """Run the policy engine daemon."""
    check_single_instance()
    print(f"Starting {SERVICE_NAME} daemon...")
    
    create_policy_rules_table()
    
    try:
        seed_default_policies()
    except Exception as e:
        print(f"Seed may have partial duplicate (OK): {e}")
    
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == '__main__':
    run()