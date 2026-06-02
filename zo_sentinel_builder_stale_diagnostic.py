# deps: requests,psutil

"""
zo_sentinel_builder_stale_diagnostic.py

READ-ONLY diagnostic utility to investigate why zo_sentinel_builder daemon is stale.
This module provides diagnostic functions to query heartbeat status, check process
health, analyze logs, verify disk space, and check queue depth.

All operations are READ-ONLY - no writes, no restarts.
"""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

try:
    import requests
except ImportError:
    requests = None


def get_current_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse various timestamp formats to datetime object."""
    if not ts_str:
        return None

    formats = [
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue

    return None


def calculate_stale_duration(last_heartbeat: str) -> tuple:
    """Calculate how stale the service is based on last heartbeat."""
    last_ts = parse_timestamp(last_heartbeat)
    if last_ts is None:
        return "UNKNOWN", "UNKNOWN"

    now = datetime.now(timezone.utc)
    delta = now - last_ts
    total_seconds = delta.total_seconds()

    hours, remainder = divmod(int(total_seconds), 3600)
    minutes = remainder // 60
    seconds = remainder % 60

    duration_str = f"{hours}h {minutes}m {seconds}s"

    if total_seconds > 600:
        status = "STALE"
    elif total_seconds > 300:
        status = "WARNING"
    else:
        status = "OK"

    return duration_str, status


def check_service_heartbeat() -> dict:
    """Query service_health for last heartbeat via HTTP endpoint."""
    result = {
        'last_heartbeat': None,
        'stale_duration': None,
        'status': None,
        'raw_record': None,
        'error': None
    }

    if requests is None:
        result['error'] = 'requests library not available'
        return result

    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={
                'sql': 'SELECT timestamp,status,meta FROM service_health WHERE service_name=%s ORDER BY timestamp DESC LIMIT 1',
                'params': ['zo_sentinel_builder']
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if data and isinstance(data, list) and len(data) > 0:
            record = data[0]
            result['raw_record'] = record
            result['last_heartbeat'] = record.get('timestamp')

            duration, status = calculate_stale_duration(record.get('timestamp'))
            result['stale_duration'] = duration
            result['status'] = status
        elif data and isinstance(data, list) and len(data) == 0:
            result['error'] = 'No heartbeat record found'
        else:
            result['error'] = 'Unexpected response format'

    except requests.exceptions.Timeout:
        result['error'] = 'Request timeout (10s exceeded)'
    except requests.exceptions.ConnectionError:
        result['error'] = 'Connection refused on 127.0.0.1:8772'
    except requests.exceptions.HTTPError as e:
        result['error'] = f'HTTP error: {e}'
    except Exception as e:
        result['error'] = f'Unexpected error: {str(e)}'

    return result


def check_builder_process() -> dict:
    """Check if builder process is running using psutil."""
    result = {
        'running': False,
        'count': 0,
        'processes': [],
        'error': None
    }

    if psutil is None:
        result['error'] = 'psutil library not available'
        return result

    try:
        builder_processes = []
        target_names = ['builder', 'zo_sentinel_builder', 'sentinel_builder']

        for proc in psutil.process_iter(['pid', 'name', 'create_time', 'status']):
            try:
                proc_info = proc.info
                proc_name = proc_info.get('name', '').lower()

                if any(tn in proc_name for tn in target_names):
                    uptime_seconds = None
                    create_time = proc_info.get('create_time')
                    if create_time:
                        uptime_seconds = time.time() - create_time

                    builder_processes.append({
                        'pid': proc_info.get('pid'),
                        'name': proc_info.get('name'),
                        'uptime_seconds': uptime_seconds,
                        'uptime_formatted': _format_uptime(uptime_seconds) if uptime_seconds else 'N/A',
                        'status': proc_info.get('status')
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        result['running'] = len(builder_processes) > 0
        result['count'] = len(builder_processes)
        result['processes'] = builder_processes

    except Exception as e:
        result['error'] = f'Error iterating processes: {str(e)}'

    return result


def _format_uptime(seconds: float) -> str:
    """Format uptime seconds into human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def _find_builder_log() -> str:
    """Find the builder log file path checking multiple locations."""
    log_paths = [
        './logs/zo_sentinel_builder.log',
        './zo_sentinel_builder.log',
        './logs/zo_sentinel_builder.out',
        './zo_sentinel_builder.out',
        '/var/log/zo_sentinel_builder.log',
        '/var/log/zo_sentinel_builder.out',
    ]

    for path in log_paths:
        if os.path.exists(path):
            return path

    logs_dir = './logs'
    if os.path.isdir(logs_dir):
        try:
            for entry in os.listdir(logs_dir):
                if entry.endswith('.log'):
                    return os.path.join(logs_dir, entry)
        except OSError:
            pass

    return log_paths[0]


def inspect_builder_logs() -> dict:
    """Inspect builder logs for recent errors."""
    result = {
        'log_file': None,
        'exists': False,
        'total_lines': 0,
        'errors_found': 0,
        'fatal_found': 0,
        'recent_errors': [],
        'last_error': None,
        'read_error': None
    }

    log_path = _find_builder_log()
    result['log_file'] = log_path

    if not os.path.exists(log_path):
        result['read_error'] = f'Log file not found: {log_path}'
        return result

    result['exists'] = True

    try:
        encodings = ['utf-8', 'latin-1', 'cp1252']
        content = None

        for encoding in encodings:
            try:
                with open(log_path, 'r', encoding=encoding, errors='replace') as f:
                    lines = f.readlines()
                content = lines
                break
            except (UnicodeDecodeError, IOError):
                continue

        if content is None:
            result['read_error'] = 'Failed to read log with any encoding'
            return result

        result['total_lines'] = len(content)
        recent_lines = content[-100:] if len(content) > 100 else content

        error_patterns = ['ERROR', 'FATAL', 'CRITICAL', 'Exception', 'Traceback']
        error_lines = []

        for line in recent_lines:
            line_upper = line.upper()
            if any(pattern in line_upper for pattern in error_patterns):
                error_lines.append(line.strip())

                if any(p in line_upper for p in ['ERROR', 'FATAL', 'CRITICAL']):
                    result['errors_found'] += 1
                if 'FATAL' in line_upper or 'CRITICAL' in line_upper:
                    result['fatal_found'] += 1

        result['recent_errors'] = error_lines[-10:]
        result['last_error'] = error_lines[-1] if error_lines else None

    except IOError as e:
        result['read_error'] = f'IOError reading log: {str(e)}'
    except Exception as e:
        result['read_error'] = f'Error reading log: {str(e)}'

    return result


def check_disk_space() -> dict:
    """Check disk space availability using psutil or shutil."""
    result = {
        'available_bytes': None,
        'available_gb': None,
        'total_bytes': None,
        'used_percent': None,
        'path': None,
        'error': None
    }

    try:
        path = os.getcwd()
        result['path'] = path

        if psutil:
            usage = psutil.disk_usage(path)
            result['available_bytes'] = usage.free
            result['available_gb'] = round(usage.free / (1024 ** 3), 2)
            result['total_bytes'] = usage.total
            result['used_percent'] = round(usage.percent, 1)
        else:
            usage = shutil.disk_usage(path)
            result['available_bytes'] = usage.free
            result['available_gb'] = round(usage.free / (1024 ** 3), 2)
            result['total_bytes'] = usage.total
            result['used_percent'] = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0

    except Exception as e:
        result['error'] = f'Error checking disk space: {str(e)}'

    return result


def check_queue_depth() -> dict:
    """Query build_queue table to check pending builds count."""
    result = {
        'queue_depth': None,
        'error': None
    }

    if requests is None:
        result['error'] = 'requests library not available'
        return result

    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={
                'sql': 'SELECT COUNT(*) as cnt FROM build_queue',
                'params': []
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if data and isinstance(data, list) and len(data) > 0:
            result['queue_depth'] = data[0].get('cnt', 0)
        elif data and isinstance(data, list) and len(data) == 0:
            result['queue_depth'] = 0
        else:
            result['error'] = 'Unexpected response format'

    except requests.exceptions.Timeout:
        result['error'] = 'Request timeout (10s exceeded)'
    except requests.exceptions.ConnectionError:
        result['error'] = 'Connection refused on 127.0.0.1:8772'
    except requests.exceptions.HTTPError as e:
        result['error'] = f'HTTP error: {e}'
    except Exception as e:
        result['error'] = f'Unexpected error: {str(e)}'

    return result


def run_all_diagnostics() -> dict:
    """Run all diagnostic checks and return consolidated results."""
    return {
        'report_timestamp': get_current_timestamp(),
        'heartbeat': check_service_heartbeat(),
        'process': check_builder_process(),
        'logs': inspect_builder_logs(),
        'disk': check_disk_space(),
        'queue': check_queue_depth()
    }


def format_report(diagnostics: dict) -> str:
    """Format diagnostic results into human-readable report."""
    lines = []
    sep = "=" * 45

    lines.append(sep)
    lines.append("ZO_SENTINEL_BUILDER STALE DIAGNOSTIC")
    lines.append(sep)
    lines.append(f"Timestamp: {diagnostics['report_timestamp']}")
    lines.append("")

    lines.append("[1] HEARTBEAT STATUS")
    hb = diagnostics['heartbeat']
    if hb.get('error'):
        lines.append(f"   Error: {hb['error']}")
    else:
        lines.append(f"   Last heartbeat: {hb.get('last_heartbeat', 'N/A')}")
        lines.append(f"   Stale duration: {hb.get('stale_duration', 'N/A')}")
        lines.append(f"   Status: {hb.get('status', 'N/A')}")

    lines.append("")
    lines.append("[2] PROCESS STATUS")
    proc = diagnostics['process']
    if proc.get('error'):
        lines.append(f"   Error: {proc['error']}")
    else:
        running = "Yes" if proc.get('running') else "No"
        lines.append(f"   Running: {running}")
        lines.append(f"   Process count: {proc.get('count', 0)}")
        if proc.get('processes'):
            for p in proc['processes']:
                lines.append(f"   - PID {p['pid']}: {p['name']} (uptime: {p['uptime_formatted']})")

    lines.append("")
    lines.append("[3] LOG ANALYSIS")
    log = diagnostics['logs']
    if log.get('read_error') and not log.get('exists'):
        lines.append(f"   Error: {log['read_error']}")
    else:
        lines.append(f"   Log file: {log.get('log_file', 'N/A')}")
        lines.append(f"   Total lines: {log.get('total_lines', 0)}")
        lines.append(f"   Recent errors: {log.get('errors_found', 0)}")
        lines.append(f"   Fatal errors: {log.get('fatal_found', 0)}")
        if log.get('last_error'):
            error_preview = log['last_error'][:100] + ('...' if len(log['last_error']) > 100 else '')
            lines.append(f"   Last error: {error_preview}")
        else:
            lines.append(f"   Last error: (none)")

    lines.append("")
    lines.append("[4] DISK SPACE")
    disk = diagnostics['disk']
    if disk.get('error'):
        lines.append(f"   Error: {disk['error']}")
    else:
        avail_gb = disk.get('available_gb', 'N/A')
        used_pct = disk.get('used_percent', 'N/A')
        lines.append(f"   Available: {avail_gb} GB ({used_pct}% used)")

    lines.append("")
    lines.append("[5] QUEUE DEPTH")
    queue = diagnostics['queue']
    if queue.get('error'):
        lines.append(f"   Error: {queue['error']}")
    else:
        depth = queue.get('queue_depth', 'N/A')
        lines.append(f"   Pending builds: {depth}")

    lines.append("")
    lines.append(sep)
    lines.append("DIAGNOSTIC COMPLETE")
    lines.append(sep)

    return '\n'.join(lines)


def get_machine_readable(diagnostics: dict) -> str:
    """Return machine-readable JSON representation of diagnostics."""
    output = {
        'status': 'complete',
        'report_timestamp': diagnostics['report_timestamp'],
        'findings': {
            'heartbeat': diagnostics['heartbeat'],
            'process': {
                'running': diagnostics['process'].get('running'),
                'count': diagnostics['process'].get('count'),
                'processes': diagnostics['process'].get('processes', []),
                'error': diagnostics['process'].get('error')
            },
            'logs': {
                'log_file': diagnostics['logs'].get('log_file'),
                'exists': diagnostics['logs'].get('exists'),
                'total_lines': diagnostics['logs'].get('total_lines'),
                'errors_found': diagnostics['logs'].get('errors_found'),
                'fatal_found': diagnostics['logs'].get('fatal_found'),
                'last_error': diagnostics['logs'].get('last_error'),
                'recent_errors': diagnostics['logs'].get('recent_errors', []),
                'error': diagnostics['logs'].get('read_error')
            },
            'disk': {
                'available_gb': diagnostics['disk'].get('available_gb'),
                'used_percent': diagnostics['disk'].get('used_percent'),
                'path': diagnostics['disk'].get('path'),
                'error': diagnostics['disk'].get('error')
            },
            'queue': {
                'depth': diagnostics['queue'].get('queue_depth'),
                'error': diagnostics['queue'].get('error')
            }
        }
    }

    return json.dumps(output, indent=2)


if __name__ == '__main__':
    import tempfile
    import unittest
    from unittest.mock import patch, MagicMock

    class TestDiagnostic(unittest.TestCase):
        """Self-smoke test for the diagnostic utility."""

        def setUp(self):
            self.mock_heartbeat_response = [
                {'timestamp': '2026-06-01T03:28:00Z', 'status': 'active', 'meta': '{}'}
            ]
            self.mock_queue_response = [{'cnt': 0}]

        @patch('zo_sentinel_builder_stale_diagnostic.requests.post')
        @patch('zo_sentinel_builder_stale_diagnostic.psutil.process_iter')
        @patch('zo_sentinel_builder_stale_diagnostic.os.path.exists')
        @patch('zo_sentinel_builder_stale_diagnostic.os.getcwd')
        @patch('zo_sentinel_builder_stale_diagnostic.psutil.disk_usage')
        @patch('builtins.open', create=True)
        def test_full_diagnostic(
            self, mock_open, mock_disk, mock_cwd, mock_exists,
            mock_process_iter, mock_post
        ):
            mock_post.return_value = MagicMock()
            mock_post.return_value.json.side_effect = [
                self.mock_heartbeat_response,
                self.mock_queue_response
            ]
            mock_post.return_value.raise_for_status = MagicMock()

            mock_proc = MagicMock()
            mock_proc.info = {
                'pid': 12345,
                'name': 'zo_sentinel_builder',
                'create_time': time.time() - 86400,
                'status': 'running'
            }
            mock_process_iter.return_value = [mock_proc]

            mock_exists.return_value = True

            log_content = "2026-06-01 INFO: Service started\n2026-06-02 ERROR: Connection timeout\n2026-06-02 FATAL: Out of memory"
            mock_open.return_value.__enter__.return_value.readlines.return_value = log_content.split('\n')

            mock_cwd.return_value = '/test/path'

            mock_usage = MagicMock()
            mock_usage.free = 50 * (1024 ** 3)
            mock_usage.total = 100 * (1024 ** 3)
            mock_usage.percent = 50.0
            mock_disk.return_value = mock_usage

            diagnostics = run_all_diagnostics()
            report = format_report(diagnostics)
            json_output = get_machine_readable(diagnostics)

            self.assertIn('ZO_SENTINEL_BUILDER STALE DIAGNOSTIC', report)
            self.assertIn('[1] HEARTBEAT STATUS', report)
            self.assertIn('[2] PROCESS STATUS', report)
            self.assertIn('[3] LOG ANALYSIS', report)
            self.assertIn('[4] DISK SPACE', report)
            self.assertIn('[5] QUEUE DEPTH', report)
            self.assertIn('DIAGNOSTIC COMPLETE', report)

            json_data = json.loads(json_output)
            self.assertEqual(json_data['status'], 'complete')
            self.assertIn('findings', json_data)

            self.assertIn('heartbeat', json_data['findings'])
            self.assertIn('process', json_data['findings'])
            self.assertIn('logs', json_data['findings'])
            self.assertIn('disk', json_data['findings'])
            self.assertIn('queue', json_data['findings'])

            print("\n" + "=" * 50)
            print("SELF-SMOKE TEST PASSED")
            print("=" * 50)
            print("\nDiagnostic Report Preview:")
            print(report[:500] + "..." if len(report) > 500 else report)
            print("\nJSON Output Keys:", list(json_data.keys()))
            print("Findings Keys:", list(json_data['findings'].keys()))

    suite = unittest.TestLoader().loadTestsFromTestCase(TestDiagnostic)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    exit(0 if result.wasSuccessful() else 1)