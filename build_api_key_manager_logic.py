import logging
import os
import secrets
import hashlib
import time
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/api_key_manager.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'api_key_manager'
PORT = 8786
PID_FILE = '/tmp/api_key_manager.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'

app = FastAPI(title='API Key Manager', version='1.0.0')

TOKEN_PREFIX = 'zsk_'
TOKEN_LENGTH = 32

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'ws_query failed: {e}')
        raise

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/write', json={'table': table, 'rows': rows}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'ws_write failed: {e}')
        raise

def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'ws_execute failed: {e}')
        raise

def generate_token_id() -> str:
    random_bytes = secrets.token_bytes(TOKEN_LENGTH)
    token_hash = hashlib.sha256(random_bytes).hexdigest()[:TOKEN_LENGTH]
    return f'{TOKEN_PREFIX}{token_hash}'

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f'Another instance running with PID {old_pid}')
            return False
        except (OSError, ValueError):
            logger.warning(f'Stale PID file, removing')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    return True

def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except OSError as e:
        logger.error(f'Failed to remove PID file: {e}')

def signal_handler(signum, frame):
    logger.info(f'Received signal {signum}, shutting down')
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    try:
        row = {
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso(),
            'status': 'running',
            'meta': '{}'
        }
        ws_write('service_health', [row])
    except Exception as e:
        logger.error(f'Heartbeat failed: {e}')

def ensure_auth_tokens_table():
    sql = """
    CREATE TABLE IF NOT EXISTS api_key_metadata (
        key_id VARCHAR PRIMARY KEY,
        key_hash VARCHAR NOT NULL,
        key_prefix VARCHAR NOT NULL,
        admin_email VARCHAR NOT NULL,
        description VARCHAR,
        scopes VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        is_active BOOLEAN DEFAULT TRUE,
        revoked_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)
    logger.info('Ensured api_key_metadata table exists')

class CreateKeyRequest(BaseModel):
    admin_email: str
    description: Optional[str] = None
    scopes: List[str] = ['read']
    expires_in_days: Optional[int] = 365

class RevokeKeyRequest(BaseModel):
    key_id: str
    admin_email: str

class ValidateKeyRequest(BaseModel):
    token: str

class KeyInfo(BaseModel):
    key_id: str
    key_prefix: str
    admin_email: str
    description: Optional[str]
    scopes: List[str]
    created_at: str
    expires_at: Optional[str]
    last_used_at: Optional[str]
    is_active: bool

def verify_admin_auth(authorization: str = Header(...)) -> str:
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Invalid authorization header format')
    token = authorization[7:]
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail='Invalid token format')
    key_hash = hash_token(token)
    result = ws_query(f"""
        SELECT key_id, admin_email, scopes, expires_at, is_active
        FROM api_key_metadata
        WHERE key_hash = '{key_hash}' AND is_active = TRUE
    """)
    rows = result.get('rows', [])
    if not rows:
        raise HTTPException(status_code=401, detail='Invalid or revoked token')
    row = rows[0]
    expires_at = row.get('expires_at')
    if expires_at:
        if isinstance(expires_at, str):
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        else:
            exp_dt = expires_at
        if datetime.now(timezone.utc) > exp_dt:
            raise HTTPException(status_code=401, detail='Token expired')
    return row['admin_email']

@app.post('/keys', status_code=201)
async def create_api_key(req: CreateKeyRequest):
    key_id = generate_token_id()
    token_plain = key_id
    key_hash = hash_token(token_plain)
    key_prefix = key_id[:12] + '****'
    scopes_str = ','.join(req.scopes)
    created_at = utc_now_iso()
    expires_at = None
    if req.expires_in_days:
        exp_dt = datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)
        expires_at = exp_dt.isoformat()
    row = {
        'key_id': key_id,
        'key_hash': key_hash,
        'key_prefix': key_prefix,
        'admin_email': req.admin_email,
        'description': req.description or '',
        'scopes': scopes_str,
        'created_at': created_at,
        'expires_at': expires_at,
        'is_active': True
    }
    ws_write('api_key_metadata', [row])
    logger.info(f'Created API key {key_prefix} for {req.admin_email}')
    return {
        'key_id': key_id,
        'key_prefix': key_prefix,
        'token': token_plain,
        'admin_email': req.admin_email,
        'scopes': req.scopes,
        'created_at': created_at,
        'expires_at': expires_at,
        'warning': 'Store this token securely. It will not be shown again.'
    }

@app.get('/keys')
async def list_keys(admin_email: str, _: str = Depends(verify_admin_auth)):
    result = ws_query(f"""
        SELECT key_id, key_prefix, admin_email, description, scopes, 
               created_at, expires_at, last_used_at, is_active, revoked_at
        FROM api_key_metadata
        WHERE admin_email = '{admin_email}'
        ORDER BY created_at DESC
    """)
    rows = result.get('rows', [])
    keys = []
    for row in rows:
        scopes = row.get('scopes', '').split(',') if row.get('scopes') else []
        keys.append({
            'key_id': row['key_id'],
            'key_prefix': row['key_prefix'],
            'admin_email': row['admin_email'],
            'description': row.get('description'),
            'scopes': scopes,
            'created_at': row['created_at'],
            'expires_at': row.get('expires_at'),
            'last_used_at': row.get('last_used_at'),
            'is_active': row['is_active'],
            'revoked_at': row.get('revoked_at')
        })
    return {'keys': keys, 'count': len(keys)}

@app.post('/keys/revoke')
async def revoke_key(req: RevokeKeyRequest, _: str = Depends(verify_admin_auth)):
    key_hash = hash_token(req.token) if hasattr(req, 'token') else None
    if hasattr(req, 'key_id') and not key_hash:
        result = ws_query(f"SELECT key_hash FROM api_key_metadata WHERE key_id = '{req.key_id}'")
        rows = result.get('rows', [])
        if rows:
            key_hash = rows[0]['key_hash']
    if not key_hash:
        raise HTTPException(status_code=404, detail='Key not found')
    now = utc_now_iso()
    sql = f"""
        UPDATE api_key_metadata 
        SET is_active = FALSE, revoked_at = '{now}'
        WHERE key_hash = '{key_hash}'
    """
    ws_execute(sql)
    logger.info(f'Revoked API key for {req.admin_email}')
    return {'status': 'revoked', 'key_id': req.key_id, 'revoked_at': now}

@app.post('/keys/validate')
async def validate_token(token: str):
    key_hash = hash_token(token)
    if not token.startswith(TOKEN_PREFIX):
        return {'valid': False, 'reason': 'Invalid token format'}
    result = ws_query(f"""
        SELECT key_id, admin_email, scopes, expires_at, is_active
        FROM api_key_metadata
        WHERE key_hash = '{key_hash}'
    """)
    rows = result.get('rows', [])
    if not rows:
        return {'valid': False, 'reason': 'Token not found'}
    row = rows[0]
    if not row['is_active']:
        return {'valid': False, 'reason': 'Token has been revoked'}
    expires_at = row.get('expires_at')
    if expires_at:
        if isinstance(expires_at, str):
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        else:
            exp_dt = expires_at
        if datetime.now(timezone.utc) > exp_dt:
            return {'valid': False, 'reason': 'Token expired'}
    ws_execute(f"""
        UPDATE api_key_metadata 
        SET last_used_at = '{utc_now_iso()}'
        WHERE key_id = '{row['key_id']}'
    """)
    scopes = row.get('scopes', '').split(',') if row.get('scopes') else []
    return {
        'valid': True,
        'key_id': row['key_id'],
        'admin_email': row['admin_email'],
        'scopes': scopes
    }

@app.get('/health')
async def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'timestamp': utc_now_iso()}

def run():
    if not check_single_instance():
        sys.exit(1)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        ensure_auth_tokens_table()
    except Exception as e:
        logger.error(f'Failed to initialize: {e}')
        remove_pid_file()
        sys.exit(1)
    import uvicorn
    logger.info(f'Starting {SERVICE_NAME} on port {PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')

if __name__ == '__main__':
    run()