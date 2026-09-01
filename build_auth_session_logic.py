import logging
import os
import sys
import time
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

SERVICE_NAME = 'auth_session_logic'
PORT = 8795
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
HEARTBEAT_INTERVAL = 60
SESSION_TIMEOUT_SECONDS = 3600
CLEANUP_INTERVAL = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_single_instance() -> bool:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f'Service already running with PID {old_pid}')
            return False
        except (OSError, ValueError):
            logger.info(f'Removing stale PID file')
            os.remove(pid_file)
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        logger.warning(f'Error removing PID file: {e}')


def signal_handler(signum, frame):
    logger.info(f'Received signal {signum}, shutting down')
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f'Query failed: {e} | SQL: {sql[:200]}')
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'Write failed: {e} | Table: {table}')
        return False


def ws_execute(sql: str) -> bool:
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=15
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'Execute failed: {e} | SQL: {sql[:200]}')
        return False


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None):
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [row])


def ensure_auth_sessions_table():
    sql = '''
    CREATE TABLE IF NOT EXISTS auth_sessions (
        session_id VARCHAR PRIMARY KEY,
        token_id VARCHAR NOT NULL,
        session_token VARCHAR NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        last_activity TIMESTAMPTZ NOT NULL,
        ip_address VARCHAR,
        user_agent VARCHAR,
        is_active BOOLEAN DEFAULT TRUE,
        terminated_at TIMESTAMPTZ
    )
    '''
    ws_execute(sql)
    logger.info('auth_sessions table ensured')


def ensure_auth_tokens_table():
    sql = '''
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id VARCHAR PRIMARY KEY,
        action VARCHAR NOT NULL,
        mcp_name VARCHAR,
        submission_id VARCHAR,
        admin_email VARCHAR NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        used BOOLEAN DEFAULT FALSE,
        used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    '''
    ws_execute(sql)
    logger.info('auth_tokens table ensured')


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def compute_session_id(token_id: str, created_at: str) -> str:
    content = f'{token_id}:{created_at}'
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def create_session(
    token_id: str,
    admin_email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_seconds: int = SESSION_TIMEOUT_SECONDS
) -> Optional[Dict[str, Any]]:
    session_token = generate_session_token()
    created_at = utc_now_iso()
    expires_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = expires_at.replace(second=expires_at.second + ttl_seconds)
    expires_at_iso = expires_at.isoformat()
    session_id = compute_session_id(token_id, created_at)
    
    row = {
        'session_id': session_id,
        'token_id': token_id,
        'session_token': session_token,
        'created_at': created_at,
        'expires_at': expires_at_iso,
        'last_activity': created_at,
        'ip_address': ip_address or 'unknown',
        'user_agent': user_agent or 'unknown',
        'is_active': True
    }
    
    if ws_write('auth_sessions', [row]):
        logger.info(f'Session created: {session_id} for token {token_id}')
        return {
            'session_id': session_id,
            'session_token': session_token,
            'expires_at': expires_at_iso
        }
    return None


def validate_session(session_token: str) -> Optional[Dict[str, Any]]:
    sql = f'''
    SELECT s.session_id, s.token_id, s.expires_at, s.is_active,
           t.action, t.mcp_name, t.admin_email, t.submission_id
    FROM auth_sessions s
    JOIN auth_tokens t ON s.token_id = t.token_id
    WHERE s.session_token = '{session_token}'
    '''
    rows = ws_query(sql)
    if not rows:
        return None
    
    session = rows[0]
    expires_at = session.get('expires_at', '')
    if expires_at:
        try:
            if 'Z' not in expires_at and '+' not in expires_at:
                expires_at = expires_at + 'Z'
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if exp_dt < datetime.now(timezone.utc):
                logger.info(f'Session expired: {session["session_id"]}')
                return None
        except Exception as e:
            logger.warning(f'Error parsing expires_at: {e}')
    
    if not session.get('is_active'):
        logger.info(f'Session inactive: {session["session_id"]}')
        return None
    
    return session


def refresh_session(session_token: str) -> bool:
    session = validate_session(session_token)
    if not session:
        return False
    
    new_expires = datetime.now(timezone.utc).replace(microsecond=0)
    new_expires = new_expires.replace(second=new_expires.second + SESSION_TIMEOUT_SECONDS)
    expires_iso = new_expires.isoformat()
    
    sql = f'''
    UPDATE auth_sessions
    SET last_activity = '{utc_now_iso()}',
        expires_at = '{expires_iso}'
    WHERE session_token = '{session_token}'
    '''
    if ws_execute(sql):
        logger.info(f'Session refreshed: {session["session_id"]}')
        return True
    return False


def terminate_session(session_token: str) -> bool:
    sql = f'''
    UPDATE auth_sessions
    SET is_active = FALSE,
        terminated_at = '{utc_now_iso()}'
    WHERE session_token = '{session_token}'
    '''
    if ws_execute(sql):
        logger.info(f'Session terminated')
        return True
    return False


def terminate_all_sessions_for_token(token_id: str) -> int:
    sql = f'''
    UPDATE auth_sessions
    SET is_active = FALSE,
        terminated_at = '{utc_now_iso()}'
    WHERE token_id = '{token_id}' AND is_active = TRUE
    '''
    if ws_execute(sql):
        logger.info(f'All sessions terminated for token: {token_id}')
        return 1
    return 0


def cleanup_expired_sessions() -> int:
    now = utc_now_iso()
    sql = f'''
    UPDATE auth_sessions
    SET is_active = FALSE,
        terminated_at = '{now}'
    WHERE is_active = TRUE AND expires_at < '{now}'
    '''
    if ws_execute(sql):
        logger.debug('Expired sessions cleaned up')
        return 1
    return 0


def get_active_session_count() -> int:
    sql = "SELECT COUNT(*) as cnt FROM auth_sessions WHERE is_active = TRUE"
    rows = ws_query(sql)
    if rows:
        return rows[0].get('cnt', 0)
    return 0


def get_sessions_by_token(token_id: str) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM auth_sessions WHERE token_id = '{token_id}' ORDER BY created_at DESC"
    return ws_query(sql)


def mark_token_used(token_id: str) -> bool:
    now = utc_now_iso()
    sql = f"UPDATE auth_tokens SET used = TRUE, used_at = '{now}' WHERE token_id = '{token_id}'"
    return ws_execute(sql)


def is_token_valid(token_id: str) -> bool:
    sql = f"SELECT token_id, expires_at, used FROM auth_tokens WHERE token_id = '{token_id}'"
    rows = ws_query(sql)
    if not rows:
        return False
    
    token = rows[0]
    if token.get('used'):
        return False
    
    expires_at = token.get('expires_at', '')
    if expires_at:
        try:
            if 'Z' not in expires_at and '+' not in expires_at:
                expires_at = expires_at + 'Z'
            exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if exp_dt < datetime.now(timezone.utc):
                return False
        except Exception:
            pass
    return True


def cycle():
    try:
        ensure_auth_sessions_table()
        ensure_auth_tokens_table()
        
        cleanup_expired_sessions()
        
        active_count = get_active_session_count()
        send_heartbeat('running', {'active_sessions': active_count})
        
    except Exception as e:
        logger.error(f'Cycle error: {e}', exc_info=True)
        send_heartbeat('error', {'error': str(e)})


def run():
    if not check_single_instance():
        sys.exit(1)
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f'{SERVICE_NAME} starting on port {PORT}')
    
    try:
        while True:
            cycle()
            time.sleep(CLEANUP_INTERVAL)
    except Exception as e:
        logger.error(f'Fatal error: {e}', exc_info=True)
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()