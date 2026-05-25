import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Resolve LOG_DIR as a Path (must be done before any reference) ────────────
_LOG_DIR_RAW = os.environ.get('ZO_SENTINEL_LOG_DIR', '/home/workspace/logs')
LOG_DIR = Path(_LOG_DIR_RAW)          # ← fixed: ensure Path, not str
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Module-level logger ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_DIR / 'snow_connector_workflow_integration.log')),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
SERVICE_NAME = 'snow_connector_workflow_integration'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
PORT = 8786   # integration-layer port, not a listening port

# ── Import snow_connector after LOG_DIR is safely a Path ─────────────────────
try:
    from snow_connector import (
        update_servicenow_ticket,
        create_servicenow_incident,
        get_servicenow_ticket_status,
        SERVICE_NOW_BASE_URL,
    )
    SNOW_CONNECTOR_AVAILABLE = True
    logger.info('snow_connector imported successfully. Base URL: %s', SERVICE_NOW_BASE_URL)
except ImportError as exc:
    logger.warning('snow_connector not importable (ServiceNow calls will be stubbed): %s', exc)
    SNOW_CONNECTOR_AVAILABLE = False
    update_servicenow_ticket = None   # type: ignore[assignment]
    create_servicenow_incident = None  # type: ignore[assignment]
    get_servicenow_ticket_status = None  # type: ignore[assignment]
    SERVICE_NOW_BASE_URL = 'NOT_CONFIGURED'

# ── PID / single-instance guard ───────────────────────────────────────────────
def check_single_instance() -> None:
    pid = str(os.getpid())
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old = pid_path.read_text().strip()
        # Check if that PID is still alive
        if old and os.path.exists(f'/proc/{old}'):
            logger.error('Another instance is already running (PID %s). Exiting.', old)
            sys.exit(1)
        else:
            logger.warning('Stale PID file found (%s). Overwriting.', old)
    pid_path.write_text(pid)
    logger.info('Acquired PID file: %s', PID_FILE)


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
        logger.info('Removed PID file.')
    except OSError as exc:
        logger.warning('Failed to remove PID file: %s', exc)


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    logger.info('Caught %s, shutting down gracefully.', sig_name)
    remove_pid_file()
    sys.exit(0)


# ── Write Service helpers ─────────────────────────────────────────────────────
def ws_write(table: str, rows: list) -> bool:
    """POST to write_service with explicit timeout."""
    import requests
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows},
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug('ws_write(%s) -> %s', table, resp.json())
        return True
    except Exception as exc:
        logger.error('ws_write(%s) failed: %s', table, exc)
        return False


def ws_query(sql: str) -> list:
    """POST to write_service query endpoint with explicit timeout."""
    import requests
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/query',
            json={'sql': sql},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as exc:
        logger.error('ws_query failed [%s]: %s', sql[:120], exc)
        return []


# ── ServiceNow action helpers ─────────────────────────────────────────────────
def trigger_snow_update(server_id: str, verdict: str, decision: str, approver: str) -> None:
    """
    Called on every approval decision. Calls snow_connector to update ServiceNow.
    Never calls ServiceNow directly — all via snow_connector functions.
    """
    if not SNOW_CONNECTOR_AVAILABLE:
        logger.warning('snow_connector unavailable — skipping ServiceNow update for server_id=%s', server_id)
        return

    try:
        # Log the intent to service_health meta
        logger.info(
            'ServiceNow update triggered: server=%s verdict=%s decision=%s approver=%s',
            server_id, verdict, decision, approver,
        )

        # Try to find a linked ServiceNow ticket via audit_log
        ticket_id = _find_snow_ticket_id(server_id)

        if ticket_id:
            # Update existing ticket
            result = update_servicenow_ticket(
                ticket_id=ticket_id,
                status='resolved' if decision == 'APPROVED' else 'pending',
                resolution_notes=f'Verdict={verdict} decision={decision} by={approver} at={datetime.now(timezone.utc).isoformat()}',
            )
            logger.info('ServiceNow ticket %s updated: %s', ticket_id, result)
            _log_snow_action(server_id, ticket_id, 'updated', str(result))
        else:
            # Create a new incident
            incident_id = create_servicenow_incident(
                short_description=f'Sentinel Approval: {verdict} — {server_id}',
                description=(
                    f'Server: {server_id}\nVerdict: {verdict}\nDecision: {decision}\n'
                    f'Approver: {approver}\nTimestamp: {datetime.now(timezone.utc).isoformat()}'
                ),
                priority='high' if verdict in ('UNTRUSTED', 'KNOWN_THREAT') else 'medium',
            )
            logger.info('ServiceNow incident %s created for server_id=%s', incident_id, server_id)
            _log_snow_action(server_id, incident_id, 'created', str(incident_id))

    except Exception as exc:
        logger.error('ServiceNow update failed for server_id=%s: %s', server_id, exc)
        _log_snow_action(server_id, ticket_id or 'N/A', 'failed', str(exc))


def _find_snow_ticket_id(server_id: str) -> Optional[str]:
    """Look up a ServiceNow ticket ID linked to this server from audit_log."""
    rows = ws_query(
        f"SELECT detail FROM audit_log "
        f"WHERE target_server_id = '{server_id}' "
        f"AND event_type = 'snow_ticket_created' "
        f"ORDER BY created_at DESC LIMIT 1"
    )
    if rows:
        import json as _json
        try:
            return _json.loads(rows[0]['detail'])['ticket_id']
        except Exception:
            return None
    return None


def _log_snow_action(server_id: str, ticket_id: str, action: str, result: str) -> None:
    """Record ServiceNow interaction into audit_log."""
    ws_write('audit_log', [{
        'target_server_id': server_id,
        'event_type': f'snow_ticket_{action}',
        'actor': SERVICE_NAME,
        'detail': '{"ticket_id":"' + ticket_id + '","result":"' + result + '"}',
        'created_at': datetime.now(timezone.utc).isoformat(),
    }])


# ── Approval workflow callback hook ───────────────────────────────────────────
def on_decision_made(server_id: str, verdict: str, decision: str, approver: str = 'system') -> None:
    """
    Extension point called by approval_workflow when a verdict decision is made.
    The approval_workflow module already calls this if it finds the function.
    """
    if decision == 'APPROVED':
        logger.info('APPROVED verdict for %s — triggering ServiceNow update.', server_id)
        trigger_snow_update(server_id, verdict, decision, approver)
    else:
        logger.info(
            'Decision=%s for server_id=%s — ServiceNow update skipped.',
            decision, server_id,
        )


# ── Heartbeat ─────────────────────────────────────────────────────────────────
def send_heartbeat() -> None:
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'status': 'ok',
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': '{"snow_connector_available":' + str(SnOW_CONNECTOR_AVAILABLE).lower() + '}',
    }])


# ── Cycle (one unit of work) ──────────────────────────────────────────────────
POLL_SECS = 60


def cycle() -> None:
    """
    Polls for pending approval decisions that need ServiceNow handling.
    In normal operation this is called by approval_workflow directly via on_decision_made.
    The daemon cycle catches any decisions that arrived while the workflow was offline.
    """
    pending_rows = ws_query(
        "SELECT server_id, verdict, decision, approver FROM audit_log "
        "WHERE event_type = 'approval_decision' "
        "AND created_at > datetime('now', '-1 hour') "
        "AND detail NOT LIKE '%snow_processed%'"
    )
    processed = 0
    for row in pending_rows:
        server_id = row.get('server_id', '')
        verdict = row.get('verdict', '')
        decision = row.get('decision', '')
        approver = row.get('approver', 'unknown')
        if decision == 'APPROVED':
            # Mark as processed first to avoid double-trigger
            ws_write('audit_log', [{
                'target_server_id': server_id,
                'event_type': 'snow_processed',
                'actor': SERVICE_NAME,
                'detail': '{"processed_by":"' + SERVICE_NAME + '"}',
                'created_at': datetime.now(timezone.utc).isoformat(),
            }])
            trigger_snow_update(server_id, verdict, decision, approver)
            processed += 1

    if processed:
        logger.info('Cycle processed %d pending approval(s).', processed)


# ── Daemon run loop ───────────────────────────────────────────────────────────
def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info('Starting %s daemon. Poll interval=%ds.', SERVICE_NAME, POLL_SECS)

    while True:
        try:
            cycle()
        except Exception as exc:
            logger.error('Cycle error: %s', exc, exc_info=True)
        finally:
            send_heartbeat()
            time.sleep(POLL_SECS)


# ── Health endpoint (for external probes) ─────────────────────────────────────
from fastapi import FastAPI
app = FastAPI()


@app.get('/health')
def health():
    import os as _os
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'uptime_seconds': _os.path.exists(PID_FILE) and _os.popen(
            f'ps -p $(cat {PID_FILE}) -o etimes= 2>/dev/null'
        ).read().strip() or 'unknown',
        'snow_connector_available': SnOW_CONNECTOR_AVAILABLE,
    }


if __name__ == '__main__':
    run()
    # Note: run() loops forever; uvicorn not used here.
    # Launch via: nohup python3 /home/workspace/zo_sentinel/snow_connector_workflow_integration.py &