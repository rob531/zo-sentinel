#!/usr/bin/env python3
"""
mcp_traffic_fingerprints_wirer.py

Integration module that wires mcp_traffic_fingerprints.py into mcp_scanner.
Detects MCP protocol traffic in candidate server responses and writes
confirmation signals to mcp_signal_scores table.

No direct DB access. All writes via write_service HTTP API.
"""

import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import requests

# Add project path for fingerprints import
sys.path.insert(0, '/home/workspace/zo_sentinel')
import mcp_traffic_fingerprints as mcpfp

SERVICE_NAME = 'mcp_traffic_fingerprints_wirer'
WRITE_SERVICE = 'http://127.0.0.1:8772'
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300  # 5 minutes
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)


def check_single_instance():
    """Ensure only one instance runs at a time."""
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)
            log.warning('Already running with PID %d', old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    open(PID_FILE, 'w').write(str(os.getpid()))

    def cleanup(sig, frame):
        remove_pid_file()
        sys.exit(0)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def remove_pid_file():
    """Clean up PID file on exit."""
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def ws_write(table: str, row: Dict[str, Any]) -> bool:
    """Write to DuckDB via write_service."""
    try:
        r = requests.post(
            WRITE_SERVICE + '/write',
            json={'table': table, 'rows': row, 'wait': True},
            timeout=15
        )
        r.raise_for_status()
        return r.status_code == 200
    except Exception as e:
        log.error('ws_write failed for %s: %s', table, e)
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    try:
        r = requests.post(
            WRITE_SERVICE + '/query',
            json={'sql': sql},
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get('rows', [])
        return []
    except Exception as e:
        log.error('ws_query failed: %s', e)
        return []


def send_heartbeat():
    """Send heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)


def get_scanner_candidates() -> List[Dict[str, Any]]:
    """Fetch candidate servers from mcp_scanner output that need MCP traffic confirmation."""
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict
    FROM mcp_server_registry
    WHERE url IS NOT NULL
      AND url != ''
      AND verdict IS NULL
       OR verdict = 'unknown'
    LIMIT 500
    """
    return ws_query(sql)


def analyze_server_for_mcp_traffic(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Analyze a server URL for MCP protocol traffic confirmation.
    Makes HTTP request and checks response for MCP JSON-RPC patterns.
    """
    url = server.get('url')
    if not url:
        return None

    # Normalize URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    server_id = server.get('server_id')
    name = server.get('name', '')

    result = {
        'server_id': server_id,
        'name': name,
        'url': url,
        'mcp_confirmed': False,
        'methods_detected': [],
        'session_indicators': {},
        'confidence': 0.0,
        'evidence_blob': {}
    }

    try:
        # Attempt to get MCP endpoint info
        endpoints_to_try = [
            url.rstrip('/') + '/mcp',
            url.rstrip('/') + '/.well-known/mcp',
            url,
        ]

        for endpoint in endpoints_to_try:
            try:
                response = requests.get(
                    endpoint,
                    timeout=10,
                    headers={'Accept': 'application/json'},
                    allow_redirects=True
                )

                if response.status_code in (200, 201):
                    content = response.text

                    # Check if content contains MCP traffic
                    if mcpfp.is_mcp_traffic(content):
                        result['mcp_confirmed'] = True

                        # Extract detected MCP methods
                        methods = mcpfp.detect_mcp_methods(content)
                        result['methods_detected'] = methods

                        # Extract session indicators
                        sessions = mcpfp.extract_session_indicators(content)
                        if sessions:
                            result['session_indicators'] = sessions

                        # Compute confidence based on methods found
                        if methods:
                            result['confidence'] = min(1.0, len(methods) * 0.25)

                        result['evidence_blob'] = {
                            'endpoint_tested': endpoint,
                            'status_code': response.status_code,
                            'content_length': len(content),
                            'detected_methods': methods,
                            'session_count': len(sessions) if sessions else 0
                        }
                        break

            except requests.RequestException:
                continue

    except Exception as e:
        log.debug('Error analyzing %s: %s', url, e)

    return result


def write_mcp_traffic_signal(analysis: Dict[str, Any]) -> bool:
    """Write MCP traffic confirmation signal to mcp_signal_scores table."""
    if not analysis or not analysis.get('server_id'):
        return False

    signal_type = 'mcp_traffic_confirmation'
    server_id = analysis['server_id']

    # Get existing score if any
    existing = ws_query(f"""
        SELECT score, evidence 
        FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' 
          AND signal_name = '{signal_type}'
        LIMIT 1
    """)

    evidence_blob = json.dumps({
        'mcp_confirmed': analysis.get('mcp_confirmed', False),
        'methods_detected': analysis.get('methods_detected', []),
        'session_indicators': analysis.get('session_indicators', {}),
        'url_tested': analysis.get('url', ''),
        'evidence': analysis.get('evidence_blob', {})
    })

    if existing:
        # Update existing score
        score = analysis.get('confidence', 0.0) if analysis.get('mcp_confirmed') else 0.0
        sql = f"""
        UPDATE mcp_signal_scores 
        SET score = {score},
            evidence = '{evidence_blob.replace("'", "''")}',
            scored_at = '{datetime.now(timezone.utc).isoformat()}'
        WHERE server_id = '{server_id}' 
          AND signal_name = '{signal_type}'
        """
    else:
        # Insert new score
        score = analysis.get('confidence', 0.0) if analysis.get('mcp_confirmed') else 0.0
        row = {
            'server_id': server_id,
            'signal_name': signal_type,
            'score': score,
            'evidence': evidence_blob,
            'scored_at': datetime.now(timezone.utc).isoformat()
        }
        return ws_write('mcp_signal_scores', row)


def cycle() -> int:
    """Process one batch of candidate servers for MCP traffic confirmation."""
    log.info('Starting MCP traffic fingerprint cycle')

    candidates = get_scanner_candidates()
    if not candidates:
        log.info('No candidate servers need MCP traffic confirmation')
        return 0

    log.info('Processing %d candidate servers', len(candidates))

    processed = 0
    confirmed = 0

    for server in candidates:
        server_id = server.get('server_id')
        if not server_id:
            continue

        try:
            analysis = analyze_server_for_mcp_traffic(server)

            if analysis:
                if analysis.get('mcp_confirmed'):
                    confirmed += 1
                    log.info(
                        'MCP confirmed for %s: methods=%s',
                        server.get('name'),
                        analysis.get('methods_detected')
                    )

                # Write signal to database
                write_mcp_traffic_signal(analysis)
                processed += 1

                # Rate limit to avoid hammering servers
                time.sleep(0.5)

        except Exception as e:
            log.error('Error processing server %s: %s', server_id, e)
            continue

    log.info('Cycle complete: processed=%d, confirmed=%d', processed, confirmed)
    return processed


def run():
    """Main daemon loop."""
    log.info('Starting %s daemon', SERVICE_NAME)
    check_single_instance()

    try:
        while True:
            try:
                cycle()
            except Exception as e:
                log.error('Cycle error: %s', e)

            send_heartbeat()
            time.sleep(POLL_SECS)

    except KeyboardInterrupt:
        log.info('Received shutdown signal')
    finally:
        remove_pid_file()
        log.info('Daemon stopped')


if __name__ == '__main__':
    run()