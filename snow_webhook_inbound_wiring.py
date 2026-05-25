import logging
import os
import sys
import signal
import hashlib
import hmac
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import requests

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'snow_webhook_inbound_wiring.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('snow_webhook_inbound_wiring')

SERVICE_NAME = 'snow_webhook_inbound_wiring'
SERVICE_PORT = 8782
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
WRITE_URL = 'http://localhost:8772/write'
APPROVAL_WORKFLOW_URL = 'http://localhost:8780'
SNOW_WEBHOOK_SECRET = os.environ.get('SnowWebhookSecret', '')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def ws_write(table: str, rows: list) -> dict:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    payload = {'sql': sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_execute(sql: str) -> dict:
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_ticket_hash(ticket_id: str, short_description: str) -> str:
    content = f'{ticket_id}:{short_description}:{SNOW_WEBHOOK_SECRET}'
    return hashlib.sha256(content.encode()).hexdigest()


def validate_snow_signature(request_body: bytes, snow_signature: str) -> bool:
    if not SNOW_WEBHOOK_SECRET:
        log.warning('SnowWebhookSecret not configured; skipping signature validation')
        return True
    if not snow_signature:
        return False
    expected = hmac.new(
        SNOW_WEBHOOK_SECRET.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f'sha256={expected}', snow_signature)


def extract_mcp_server_id_from_payload(payload: dict) -> str | None:
    description = payload.get('short_description', '') or ''
    mcp_ref = payload.get('mcp_server_id') or payload.get('mcp_reference') or ''
    if not mcp_ref:
        for line in description.split():
            if line.startswith('mcp-') or line.startswith('MCP-'):
                return line.strip().rstrip('.')
    return mcp_ref or None


def parse_ticket_status(snow_state: str) -> str:
    mapping = {
        '1': 'new',
        '2': 'active',
        '3': 'awaiting_user',
        '4': 'resolved',
        '5': 'closed',
        '7': 'closed',
        '8': 'cancelled',
    }
    return mapping.get(str(snow_state), 'new')


def write_audit_event(event_type: str, detail: str, target_server_id: str | None = None) -> None:
    ts = utc_now_iso()
    row = {
        'event_type': event_type,
        'detail': detail,
        'actor': 'snow_webhook',
        'created_at': ts,
    }
    if target_server_id:
        row['target_server_id'] = target_server_id
    try:
        ws_write('audit_log', [row])
    except Exception as e:
        log.error('Failed to write audit_log: %s', e)


def ensure_submissions_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id TEXT PRIMARY KEY,
        server_id TEXT,
        source TEXT,
        mcp_name TEXT,
        mcp_url TEXT,
        description TEXT,
        submitted_by TEXT,
        ticket_id TEXT,
        snow_state TEXT,
        snow_state_label TEXT,
        approval_status TEXT DEFAULT 'pending',
        submission_ts TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        raw_payload TEXT
    )
    """
    try:
        ws_execute(create_sql)
    except Exception as e:
        log.error('Failed to ensure mcp_submissions table: %s', e)


def submission_exists(ticket_id: str) -> bool:
    sql = f"SELECT COUNT(*) as cnt FROM mcp_submissions WHERE ticket_id = '{ticket_id.replace('\'', '\'\'')}'"
    try:
        rows = ws_query(sql)
        return bool(rows and rows[0].get('cnt', 0) > 0)
    except Exception:
        return False


def upsert_submission(row: dict) -> None:
    payload_json = row.get('raw_payload', '')
    sql = f"""
    INSERT INTO mcp_submissions (
        submission_id, server_id, source, mcp_name, mcp_url,
        description, submitted_by, ticket_id, snow_state,
        snow_state_label, approval_status, submission_ts, updated_at, raw_payload
    ) VALUES (
        '{row['submission_id'].replace('\'', '\'\'')}',
        '{row.get('server_id', '').replace('\'', '\'\'')}',
        '{row.get('source', 'snow').replace('\'', '\'\'')}',
        '{row.get('mcp_name', '').replace('\'', '\'\'')}',
        '{row.get('mcp_url', '').replace('\'', '\'\'')}',
        '{row.get('description', '').replace('\'', '\'\'')}',
        '{row.get('submitted_by', '').replace('\'', '\'\'')}',
        '{row['ticket_id'].replace('\'', '\'\'')}',
        '{row.get('snow_state', '').replace('\'', '\'\'')}',
        '{row.get('snow_state_label', '').replace('\'', '\'\'')}',
        '{row.get('approval_status', 'pending').replace('\'', '\'\'')}',
        '{row.get('submission_ts', utc_now_iso()).replace('\'', '\'\'')}',
        '{utc_now_iso()}',
        '''{payload_json.replace(chr(39), chr(39)+chr(39))}'''
    )
    ON CONFLICT (submission_id) DO UPDATE SET
        snow_state = EXCLUDED.snow_state,
        snow_state_label = EXCLUDED.snow_state_label,
        approval_status = EXCLUDED.approval_status,
        updated_at = EXCLUDED.updated_at,
        raw_payload = EXCLUDED.raw_payload
    """
    try:
        ws_execute(sql)
    except Exception as e:
        log.error('Failed to upsert submission: %s', e)
        raise


def trigger_approval_workflow(submission_id: str, server_id: str | None, mcp_name: str) -> bool:
    payload = {
        'submission_id': submission_id,
        'server_id': server_id,
        'mcp_name': mcp_name,
        'source': 'snow',
    }
    try:
        resp = requests.post(
            f'{APPROVAL_WORKFLOW_URL}/submit',
            json=payload,
            timeout=20,
            headers={'Content-Type': 'application/json'}
        )
        if resp.status_code in (200, 201, 202):
            log.info('Approval workflow triggered for submission_id=%s', submission_id)
            return True
        else:
            log.warning(
                'Approval workflow returned %d for submission_id=%s: %s',
                resp.status_code, submission_id, resp.text[:200]
            )
            return False
    except requests.RequestException as e:
        log.error('Failed to trigger approval workflow for %s: %s', submission_id, e)
        return False


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        if os.path.exists(f'/proc/{old_pid}'):
            log.error('Another instance already running with PID %d. Exiting.', old_pid)
            sys.exit(1)
        log.warning('Stale PID file found; removing.')
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning('Failed to remove PID file: %s', e)


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    log.info('Received %s, shutting down gracefully.', sig_name)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> None:
    ts = utc_now_iso()
    try:
        ws_write('service_health', [{'service': SERVICE_NAME, 'last_heartbeat': ts}])
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)


def audit_log_writer(event_type: str, detail: str, target_server_id: str | None = None) -> None:
    write_audit_event(event_type, detail, target_server_id)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'ts': utc_now_iso()}


@app.post('/webhook/snow')
async def webhook_snow(
    request: Request,
    x_snow_signature: str | None = Header(None, alias='X-Snow-Signature'),
):
    """
    Inbound ServiceNow webhook receiver for MCP request tickets.
    RULES:
    1. MUST validate X-Snow-Signature header using SNOW OAuth token.
    2. MUST reject unsigned webhooks with 401.
    3. MUST parse ticket payload into mcp_submissions record.
    4. MUST trigger approval_workflow for new submission.
    5. MUST write to audit_log.
    6. MUST NOT make outbound SNOW API calls (snow_connector handles outbound).
    7. MUST be idempotent: skip already-seen ticket_id records.
    8. MUST use parameterized SQL and never interpolate raw payload via f-strings.
    """
    ts_received = utc_now_iso()
    body = await request.body()

    if not validate_snow_signature(body, x_snow_signature or ''):
        audit_log_writer('snow_webhook_rejected', 'Invalid or missing X-Snow-Signature header')
        log.warning('Rejected unsigned/invalid webhook')
        raise HTTPException(status_code=401, detail='Invalid or missing signature')

    try:
        payload = await request.json()
    except Exception as e:
        audit_log_writer('snow_webhook_rejected', f'Failed to parse JSON payload: {e}')
        raise HTTPException(status_code=400, detail='Invalid JSON payload')

    ticket_id = str(payload.get('number', payload.get('sys_id', '')))
    if not ticket_id:
        raise HTTPException(status_code=400, detail='Missing ticket number/sys_id in payload')

    short_description = str(payload.get('short_description', ''))
    state = str(payload.get('state', '1'))
    state_label = str(payload.get('state_label', payload.get('approval', 'New')))
    caller_id = str(payload.get('caller_id', payload.get('opened_by', '')))
    description = str(payload.get('description', ''))
    mcp_server_id = extract_mcp_server_id_from_payload(payload)
    mcp_name = str(payload.get('mcp_name', payload.get('u_mcp_name', '')))
    mcp_url = str(payload.get('mcp_url', payload.get('u_mcp_url', '')))

    if not mcp_name and short_description:
        words = short_description.split()
        for i, w in enumerate(words):
            if w.lower() in ('mcp', 'server', 'tool'):
                candidate = ' '.join(words[i:i+3]).strip().rstrip('.,;')
                if candidate:
                    mcp_name = candidate
                    break
        if not mcp_name:
            mcp_name = short_description[:80]

    ticket_hash = compute_ticket_hash(ticket_id, short_description)
    submission_id = f'snow-{ticket_id}'

    if submission_exists(ticket_id):
        log.info('Ticket %s already processed, updating state.', ticket_id)
        approval_status = 'pending' if state in ('1', '2') else 'updated'
        row = {
            'submission_id': submission_id,
            'server_id': mcp_server_id,
            'source': 'snow',
            'mcp_name': mcp_name,
            'mcp_url': mcp_url,
            'description': description[:500],
            'submitted_by': caller_id,
            'ticket_id': ticket_id,
            'snow_state': state,
            'snow_state_label': state_label,
            'approval_status': approval_status,
            'submission_ts': ts_received,
            'raw_payload': str(payload)[:2000],
        }
        try:
            upsert_submission(row)
            audit_log_writer(
                'snow_ticket_updated',
                f'SNOW ticket {ticket_id} state updated to {state_label}',
                mcp_server_id
            )
        except Exception as e:
            log.error('Failed to update submission %s: %s', ticket_id, e)
            raise HTTPException(status_code=500, detail='Failed to update submission')
        return {'status': 'ok', 'submission_id': submission_id, 'action': 'updated'}

    row = {
        'submission_id': submission_id,
        'server_id': mcp_server_id,
        'source': 'snow',
        'mcp_name': mcp_name,
        'mcp_url': mcp_url,
        'description': description[:500],
        'submitted_by': caller_id,
        'ticket_id': ticket_id,
        'snow_state': state,
        'snow_state_label': state_label,
        'approval_status': 'pending',
        'submission_ts': ts_received,
        'raw_payload': str(payload)[:2000],
    }

    try:
        upsert_submission(row)
    except Exception as e:
        log.error('Failed to insert submission %s: %s', ticket_id, e)
        raise HTTPException(status_code=500, detail='Failed to write submission')

    audit_log_writer(
        'snow_ticket_received',
        f'Received SNOW ticket {ticket_id}: {mcp_name} from {caller_id}',
        mcp_server_id
    )

    wf_triggered = trigger_approval_workflow(submission_id, mcp_server_id, mcp_name)
    if wf_triggered:
        audit_log_writer(
            'snow_approval_triggered',
            f'Approval workflow triggered for SNOW ticket {ticket_id}',
            mcp_server_id
        )
    else:
        log.warning('Approval workflow NOT triggered for %s; will retry on next cycle', submission_id)

    return {
        'status': 'ok',
        'submission_id': submission_id,
        'ticket_id': ticket_id,
        'mcp_name': mcp_name,
        'approval_workflow_triggered': wf_triggered,
        'received_at': ts_received,
    }


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_submissions_table()
    log.info('Starting %s on port %d', SERVICE_NAME, SERVICE_PORT)

    try:
        requests.post(
            WRITE_URL,
            json={'table': 'service_health', 'rows': [{'service': SERVICE_NAME, 'last_heartbeat': utc_now_iso()}]},
            timeout=10
        )
    except Exception as e:
        log.warning('Initial heartbeat failed: %s', e)

    uvicorn.run(
        app,
        host='0.0.0.0',
        port=SERVICE_PORT,
        log_level='info',
        access_log=True,
    )


if __name__ == '__main__':
    run()