import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'approval_verdict_gate.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'approval_verdict_gate'
PORT = 8792
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

HEARTBEAT_INTERVAL = 60
VERDICT_ALLOWED = {'TRUSTED', 'ENTERPRISE_CONTROLLED', 'AMBER_UNVERIFIED'}
VERDICT_BLOCKED = {'UNTRUSTED', 'KNOWN_THREAT', 'HIGH_RISK_ISOLATED'}

app = FastAPI()

_api_keys = {}


def load_api_keys():
    global _api_keys
    key = os.environ.get('SENTINEL_API_KEY', '')
    if key:
        _api_keys[key] = 'admin'
    logger.info('API keys loaded: %s', 'configured' if _api_keys else 'none')


def verify_api_key(authorization: str = Header(None)):
    if not _api_keys:
        return 'anonymous'
    if authorization and authorization.startswith('Bearer '):
        token = authorization[7:]
        if token in _api_keys:
            return _api_keys[token]
    raise HTTPException(status_code=401, detail='Invalid or missing API key')


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: list = None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list):
    payload = {'table': table, 'rows': rows}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str, params: list = None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_SERVICE_URL + '/execute', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error('Another instance is running with PID %s', old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            logger.warning('Stale PID file found, removing')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    logger.info('PID file written: %s = %s', PID_FILE, os.getpid())


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
        logger.info('PID file removed')
    except Exception as e:
        logger.warning('Failed to remove PID file: %s', e)


def signal_handler(signum, frame):
    logger.info('Received signal %d, shutting down gracefully', signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'running', meta: dict = None):
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'ts': utc_now_iso(),
        'meta': meta or {}
    }
    try:
        ws_write('service_health', [row])
    except Exception as e:
        logger.warning('Failed to send heartbeat: %s', e)


def ensure_tables():
    create_approval_gates_sql = """
    CREATE TABLE IF NOT EXISTS approval_verdict_gates (
        gate_id VARCHAR PRIMARY KEY,
        submission_id VARCHAR NOT NULL,
        server_id VARCHAR NOT NULL,
        verdict VARCHAR,
        risk_tier VARCHAR,
        trust_score DOUBLE,
        gate_action VARCHAR,
        gate_reason VARCHAR,
        evaluated_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    create_policy_sql = """
    CREATE TABLE IF NOT EXISTS approval_verdict_policies (
        policy_id VARCHAR PRIMARY KEY,
        policy_name VARCHAR NOT NULL,
        allowed_verdicts VARCHAR[],
        blocked_verdicts VARCHAR[],
        require_trust_score_min DOUBLE,
        allow_analyst_override BOOLEAN DEFAULT FALSE,
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    try:
        ws_execute(create_approval_gates_sql)
        ws_execute(create_policy_sql)
        logger.info('Tables ensured: approval_verdict_gates, approval_verdict_policies')
    except Exception as e:
        logger.error('Failed to ensure tables: %s', e)


def get_default_policy():
    sql = """
    SELECT policy_id, policy_name, allowed_verdicts, blocked_verdicts,
           require_trust_score_min, allow_analyst_override
    FROM approval_verdict_policies
    WHERE active = TRUE
    ORDER BY created_at DESC
    LIMIT 1
    """
    try:
        result = ws_query(sql)
        rows = result.get('rows', [])
        if rows:
            return rows[0]
    except Exception:
        pass
    return {
        'policy_id': 'default',
        'policy_name': 'default',
        'allowed_verdicts': list(VERDICT_ALLOWED),
        'blocked_verdicts': list(VERDICT_BLOCKED),
        'require_trust_score_min': 0.0,
        'allow_analyst_override': False
    }


def get_server_verdict(server_id: str):
    sql = """
    SELECT r.server_id, r.verdict, r.trust_score, r.risk_tier
    FROM mcp_server_registry r
    WHERE r.server_id = ?
    LIMIT 1
    """
    try:
        result = ws_query(sql, [server_id])
        rows = result.get('rows', [])
        if rows:
            return rows[0]
    except Exception as e:
        logger.error('Failed to query server verdict for %s: %s', server_id, e)
    return None


def evaluate_gate(submission_id: str, server_id: str, policy: dict):
    server_data = get_server_verdict(server_id)
    if not server_data:
        return {
            'gate_action': 'BLOCK',
            'gate_reason': 'Server not found in registry',
            'verdict': None,
            'risk_tier': None,
            'trust_score': None
        }

    verdict = server_data.get('verdict', 'UNKNOWN')
    trust_score = server_data.get('trust_score') or 0.0
    risk_tier = server_data.get('risk_tier')

    allowed = policy.get('allowed_verdicts') or list(VERDICT_ALLOWED)
    blocked = policy.get('blocked_verdicts') or list(VERDICT_BLOCKED)
    min_score = policy.get('require_trust_score_min', 0.0)

    if verdict in blocked:
        return {
            'gate_action': 'BLOCK',
            'gate_reason': f'Verdict {verdict} is in blocked list',
            'verdict': verdict,
            'risk_tier': risk_tier,
            'trust_score': trust_score
        }

    if verdict not in allowed and verdict != 'UNKNOWN':
        return {
            'gate_action': 'BLOCK',
            'gate_reason': f'Verdict {verdict} not in allowed list',
            'verdict': verdict,
            'risk_tier': risk_tier,
            'trust_score': trust_score
        }

    if trust_score < min_score:
        return {
            'gate_action': 'BLOCK',
            'gate_reason': f'Trust score {trust_score:.2f} below minimum {min_score:.2f}',
            'verdict': verdict,
            'risk_tier': risk_tier,
            'trust_score': trust_score
        }

    return {
        'gate_action': 'APPROVE',
        'gate_reason': f'Verdict {verdict} passes gate with score {trust_score:.2f}',
        'verdict': verdict,
        'risk_tier': risk_tier,
        'trust_score': trust_score
    }


def record_gate_event(gate_id: str, submission_id: str, server_id: str, evaluation: dict):
    sql = """
    INSERT INTO approval_verdict_gates
        (gate_id, submission_id, server_id, verdict, risk_tier, trust_score,
         gate_action, gate_reason, evaluated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (gate_id) DO UPDATE SET
        verdict = excluded.verdict,
        risk_tier = excluded.risk_tier,
        trust_score = excluded.trust_score,
        gate_action = excluded.gate_action,
        gate_reason = excluded.gate_reason,
        evaluated_at = excluded.evaluated_at
    """
    row = evaluation.copy()
    row['gate_id'] = gate_id
    row['submission_id'] = submission_id
    row['server_id'] = server_id
    row['evaluated_at'] = utc_now_iso()
    try:
        ws_execute(sql, [
            row['gate_id'],
            row['submission_id'],
            row['server_id'],
            row['verdict'],
            row['risk_tier'],
            row['trust_score'],
            row['gate_action'],
            row['gate_reason'],
            row['evaluated_at']
        ])
        logger.info('Recorded gate event: %s -> %s', gate_id, row['gate_action'])
    except Exception as e:
        logger.error('Failed to record gate event: %s', e)


class GateRequest(BaseModel):
    submission_id: str
    server_id: str
    analyst_override: bool = False
    override_reason: str = None


class PolicyCreate(BaseModel):
    policy_name: str
    allowed_verdicts: list[str] = None
    blocked_verdicts: list[str] = None
    require_trust_score_min: float = 0.0
    allow_analyst_override: bool = False


class PolicyToggle(BaseModel):
    policy_id: str
    active: bool


def compute_gate_id(submission_id: str, server_id: str):
    import hashlib
    raw = f'{submission_id}:{server_id}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'ts': utc_now_iso()}


@app.post('/gate/evaluate')
def evaluate_gate_endpoint(req: GateRequest, auth: str = Depends(verify_api_key)):
    policy = get_default_policy()
    evaluation = evaluate_gate(req.submission_id, req.server_id, policy)
    gate_id = compute_gate_id(req.submission_id, req.server_id)

    if req.analyst_override and policy.get('allow_analyst_override'):
        if req.override_reason:
            evaluation['gate_action'] = 'APPROVE'
            evaluation['gate_reason'] = f'Analyst override: {req.override_reason}'
        else:
            evaluation['gate_reason'] += ' (override attempted but no reason provided)'

    record_gate_event(gate_id, req.submission_id, req.server_id, evaluation)

    return {
        'gate_id': gate_id,
        'submission_id': req.submission_id,
        'server_id': req.server_id,
        'gate_action': evaluation['gate_action'],
        'gate_reason': evaluation['gate_reason'],
        'verdict': evaluation['verdict'],
        'risk_tier': evaluation['risk_tier'],
        'trust_score': evaluation['trust_score'],
        'evaluated_at': utc_now_iso(),
        'policy': policy.get('policy_name')
    }


@app.post('/gate/approve')
def gate_approve(req: GateRequest, auth: str = Depends(verify_api_key)):
    gate_id = compute_gate_id(req.submission_id, req.server_id)
    evaluation = evaluate_gate(req.submission_id, req.server_id, get_default_policy())

    if evaluation['gate_action'] != 'APPROVE':
        if not (req.analyst_override and get_default_policy().get('allow_analyst_override')):
            return {
                'approved': False,
                'gate_id': gate_id,
                'reason': evaluation['gate_reason'],
                'verdict': evaluation['verdict'],
                'requires_override': True
            }

    evaluation['gate_action'] = 'APPROVE'
    if req.analyst_override and req.override_reason:
        evaluation['gate_reason'] = f'Analyst override: {req.override_reason}'
    record_gate_event(gate_id, req.submission_id, req.server_id, evaluation)

    sql = """
    UPDATE approval_submissions
    SET status = 'APPROVED', approved_at = ?, approved_by = ?
    WHERE submission_id = ?
    """
    try:
        ws_execute(sql, [utc_now_iso(), auth, req.submission_id])
    except Exception as e:
        logger.warning('Could not update approval_submissions: %s', e)

    return {
        'approved': True,
        'gate_id': gate_id,
        'submission_id': req.submission_id,
        'server_id': req.server_id,
        'verdict': evaluation['verdict'],
        'trust_score': evaluation['trust_score'],
        'approved_at': utc_now_iso()
    }


@app.post('/gate/deny')
def gate_deny(req: GateRequest, auth: str = Depends(verify_api_key)):
    gate_id = compute_gate_id(req.submission_id, req.server_id)
    evaluation = evaluate_gate(req.submission_id, req.server_id, get_default_policy())

    evaluation['gate_action'] = 'DENIED'
    evaluation['gate_reason'] = req.override_reason or evaluation['gate_reason']
    record_gate_event(gate_id, req.submission_id, req.server_id, evaluation)

    sql = """
    UPDATE approval_submissions
    SET status = 'DENIED', denied_at = ?, denied_reason = ?
    WHERE submission_id = ?
    """
    try:
        ws_execute(sql, [utc_now_iso(), evaluation['gate_reason'], req.submission_id])
    except Exception as e:
        logger.warning('Could not update approval_submissions: %s', e)

    return {
        'denied': True,
        'gate_id': gate_id,
        'submission_id': req.submission_id,
        'server_id': req.server_id,
        'reason': evaluation['gate_reason'],
        'verdict': evaluation['verdict'],
        'denied_at': utc_now_iso()
    }


@app.get('/gate/history/{submission_id}')
def get_gate_history(submission_id: str, auth: str = Depends(verify_api_key)):
    sql = """
    SELECT gate_id, submission_id, server_id, verdict, risk_tier, trust_score,
           gate_action, gate_reason, evaluated_at
    FROM approval_verdict_gates
    WHERE submission_id = ?
    ORDER BY evaluated_at DESC
    LIMIT 50
    """
    try:
        result = ws_query(sql, [submission_id])
        return {'rows': result.get('rows', [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/policy')
def create_policy(policy: PolicyCreate, auth: str = Depends(verify_api_key)):
    if auth != 'admin':
        raise HTTPException(status_code=403, detail='Admin role required')
    import hashlib
    policy_id = hashlib.sha256(policy.policy_name.encode()).hexdigest()[:12]
    sql = """
    INSERT INTO approval_verdict_policies
        (policy_id, policy_name, allowed_verdicts, blocked_verdicts,
         require_trust_score_min, allow_analyst_override, active)
    VALUES (?, ?, ?, ?, ?, ?, TRUE)
    ON CONFLICT (policy_id) DO UPDATE SET
        policy_name = excluded.policy_name,
        allowed_verdicts = excluded.allowed_verdicts,
        blocked_verdicts = excluded.blocked_verdicts,
        require_trust_score_min = excluded.require_trust_score_min,
        allow_analyst_override = excluded.allow_analyst_override
    """
    try:
        ws_execute(sql, [
            policy_id,
            policy.policy_name,
            policy.allowed_verdicts or list(VERDICT_ALLOWED),
            policy.blocked_verdicts or list(VERDICT_BLOCKED),
            policy.require_trust_score_min,
            policy.allow_analyst_override
        ])
        return {'policy_id': policy_id, 'created': True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/policy')
def list_policies(auth: str = Depends(verify_api_key)):
    sql = """
    SELECT policy_id, policy_name, allowed_verdicts, blocked_verdicts,
           require_trust_score_min, allow_analyst_override, active, created_at
    FROM approval_verdict_policies
    ORDER BY created_at DESC
    """
    try:
        result = ws_query(sql)
        return {'rows': result.get('rows', [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/policy/toggle')
def toggle_policy(toggle: PolicyToggle, auth: str = Depends(verify_api_key)):
    if auth != 'admin':
        raise HTTPException(status_code=403, detail='Admin role required')
    sql = "UPDATE approval_verdict_policies SET active = ? WHERE policy_id = ?"
    try:
        ws_execute(sql, [toggle.active, toggle.policy_id])
        return {'policy_id': toggle.policy_id, 'active': toggle.active}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def heartbeat_loop():
    while True:
        try:
            send_heartbeat('running', {'port': PORT})
        except Exception as e:
            logger.warning('Heartbeat failed: %s', e)
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    load_api_keys()
    ensure_tables()
    import threading
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    logger.info('Starting %s on port %d', SERVICE_NAME, PORT)
    uvicorn.run(app, host='0.0.0.0', port=PORT)


if __name__ == '__main__':
    run()