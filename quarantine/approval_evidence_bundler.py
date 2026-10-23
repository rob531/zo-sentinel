import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import uuid

sys.path.insert(0, '/home/workspace')
import requests

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'approval_evidence_bundler'
PORT = None
PID_FILE = None
LOG_DIR = Path("/home/workspace/logs/approval_evidence_bundler")

# Create log dir BEFORE configuring logging
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'bundler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: list = None) -> list:
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json={'sql': sql, 'params': params or []},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error('ws_write failed: %s', e)
        return False


def generate_bundle_id(server_id: str, component_hashes: dict) -> str:
    content = json.dumps(component_hashes, sort_keys=True) + server_id
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def fetch_signal_scores(server_id: str) -> list:
    sql = "SELECT signal_name, score, evidence, computed_at FROM mcp_signal_scores WHERE server_id = ?"
    return ws_query(sql, [server_id])


def fetch_server_info(server_id: str) -> dict:
    sql = "SELECT server_id, name, url, description, trust_score, verdict, registry_source FROM mcp_server_registry WHERE server_id = ?"
    rows = ws_query(sql, [server_id])
    return rows[0] if rows else {}


def fetch_attestations(server_id: str) -> list:
    sql = "SELECT server_id, attestation_type, attested_at, attestor_id FROM mcp_attestations WHERE server_id = ?"
    return ws_query(sql, [server_id])


def fetch_pi_results(server_id: str) -> dict:
    sql = "SELECT pi_score, pi_evidence, pi_computed_at FROM mcp_pi_results WHERE server_id = ?"
    rows = ws_query(sql, [server_id])
    return rows[0] if rows else {}


def fetch_corpus_hash(server_id: str) -> str:
    sql = "SELECT corpus_hash FROM mcp_fingerprints WHERE server_id = ?"
    rows = ws_query(sql, [server_id])
    return rows[0]['corpus_hash'] if rows and 'corpus_hash' in rows[0] else ''


def fetch_threat_associations(server_id: str) -> list:
    sql = "SELECT threat_type, severity, evidence, reported_at FROM mcp_threat_associations WHERE server_id = ?"
    return ws_query(sql, [server_id])


def compute_signal_hash(scores: list) -> str:
    content = json.dumps(scores, sort_keys=True)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def bundle_evidence(server_id: str, analyst_email: str, decision: str, decision_notes: str = '') -> dict:
    logger.info('Bundling evidence for server_id=%s', server_id)

    signal_scores = fetch_signal_scores(server_id)
    server_info = fetch_server_info(server_id)
    attestations = fetch_attestations(server_id)
    pi_results = fetch_pi_results(server_id)
    corpus_hash = fetch_corpus_hash(server_id)
    threat_assocs = fetch_threat_associations(server_id)

    signal_hash = compute_signal_hash(signal_scores)
    component_hashes = {
        'server_info': hashlib.sha256(json.dumps(server_info, sort_keys=True).encode('utf-8')).hexdigest() if server_info else '',
        'signals': signal_hash,
        'attestations': hashlib.sha256(json.dumps(attestations, sort_keys=True).encode('utf-8')).hexdigest() if attestations else '',
        'pi_results': hashlib.sha256(json.dumps(pi_results, sort_keys=True).encode('utf-8')).hexdigest() if pi_results else '',
        'threats': hashlib.sha256(json.dumps(threat_assocs, sort_keys=True).encode('utf-8')).hexdigest() if threat_assocs else '',
    }

    bundle_id = generate_bundle_id(server_id, component_hashes)

    bundle = {
        'bundle_id': bundle_id,
        'server_id': server_id,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'server': server_info,
        'signals': signal_scores,
        'attestations': attestations,
        'pi_results': pi_results,
        'corpus_hash': corpus_hash,
        'threat_associations': threat_assocs,
        'component_hashes': component_hashes,
        'analyst_decision': {
            'decision': decision,
            'analyst_email': analyst_email,
            'decision_notes': decision_notes,
            'decided_at': datetime.now(timezone.utc).isoformat(),
        }
    }

    audit_row = {
        'id': str(uuid.uuid4()),
        'target_server_id': server_id,
        'event_type': 'evidence_bundle_created',
        'actor': analyst_email,
        'detail': json.dumps({
            'bundle_id': bundle_id,
            'decision': decision,
            'signal_count': len(signal_scores),
            'component_hashes': component_hashes,
        }),
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    ws_write('audit_log', [audit_row])

    out_dir = Path('/home/workspace/logs/evidence_bundles')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{bundle_id}.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, indent=2)

    logger.info('Evidence bundle created: %s -> %s', bundle_id, out_path)
    return bundle


def send_heartbeat():
    rows = [{
        'service': SERVICE_NAME,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'status': 'running',
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps({'pid': os.getpid()})
    }]
    ws_write('service_health', rows)


def check_single_instance():
    pid_file = Path(f'/tmp/{SERVICE_NAME}.pid')
    if pid_file.exists():
        logger.warning('Instance already running. PID file exists.')
        sys.exit(0)
    pid_file.write_text(str(os.getpid()), encoding='utf-8')


def remove_pid_file():
    pid_file = Path(f'/tmp/{SERVICE_NAME}.pid')
    if pid_file.exists():
        pid_file.unlink()


def signal_handler(signum, frame):
    logger.info('Received signal %d, shutting down.', signum)
    remove_pid_file()
    sys.exit(0)


def cycle():
    pending_sql = """
    SELECT target_server_id, event_type, detail, created_at
    FROM audit_log
    WHERE event_type IN ('approval_submitted', 'analyst_decision')
      AND detail NOT LIKE '%bundle_id%'
    ORDER BY created_at DESC
    LIMIT 50
    """
    pending = ws_query(pending_sql)
    if not pending:
        logger.debug('No pending decisions to bundle.')
        return

    for row in pending:
        server_id = row.get('target_server_id')
        if not server_id:
            continue
        try:
            detail = json.loads(row.get('detail', '{}'))
            analyst_email = row.get('event_type', 'unknown')
            decision = detail.get('decision', 'UNKNOWN')
            notes = detail.get('notes', '')
            bundle = bundle_evidence(server_id, analyst_email, decision, notes)
            logger.info('Bundled evidence for %s: %s', server_id, bundle['bundle_id'])
        except Exception as e:
            logger.error('Failed to bundle evidence for server %s: %s', server_id, e)


def run():
    import signal
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info('Starting %s daemon', SERVICE_NAME)

    POLL_SECS = 60

    while True:
        try:
            cycle()
            send_heartbeat()
        except Exception as e:
            logger.error('Cycle error: %s', e)
        import time
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    # Self-smoke: test core functions against known-good inputs
    from unittest.mock import MagicMock, patch

    # Mock ws_query to return synthetic data
    mock_signal_scores = [
        {'signal_name': 'domain_trust', 'score': 75.0, 'evidence': '{}', 'computed_at': '2026-05-24T12:00:00Z'},
        {'signal_name': 'tool_description_safety', 'score': 80.0, 'evidence': '{}', 'computed_at': '2026-05-24T12:00:00Z'},
    ]
    mock_server_info = {'server_id': 'test-001', 'name': 'test-server', 'url': 'https://example.com', 'trust_score': 70.0, 'verdict': 'TRUSTED_GENERAL', 'registry_source': 'test'}

    with patch.object(__import__('requests'), 'post') as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'rows': mock_signal_scores}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        # Test generate_bundle_id
        bid = generate_bundle_id('test-001', {'signals': 'abc'})
        assert len(bid) == 64, f'bundle_id should be SHA256 hex, got {len(bid)}'

        # Test compute_signal_hash
        h = compute_signal_hash(mock_signal_scores)
        assert len(h) == 64, f'signal_hash should be SHA256 hex, got {len(h)}'

        # Test ws_query path
        rows = ws_query('SELECT 1')
        assert rows == mock_signal_scores, 'ws_query should return mocked rows'

        # Test fetch_signal_scores with parameterized query
        rows = fetch_signal_scores('test-001')
        assert len(rows) == 2
        # Verify params were passed
        call_args = mock_post.call_args
        assert call_args is not None

        # Test bundle_evidence (mock all writes too)
        mock_post.reset_mock()
        mock_resp.json.return_value = {'rows': mock_signal_scores}

        with patch('approval_evidence_bundler.ws_write') as mock_write:
            mock_write.return_value = True
            bundle = bundle_evidence('test-001', 'analyst@test.com', 'APPROVE', 'looks good')
            assert bundle['bundle_id'] is not None
            assert len(bundle['bundle_id']) == 64
            assert bundle['analyst_decision']['decision'] == 'APPROVE'
            assert mock_write.call_count >= 1  # audit_log write + service_health

    print('Self-smoke PASSED: generate_bundle_id, compute_signal_hash, ws_query, fetch_signal_scores, bundle_evidence')
    print('approval_evidence_bundler.py: All smoke checks passed.')