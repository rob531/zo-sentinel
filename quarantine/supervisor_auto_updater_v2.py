import os
import sys
import time
import signal
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = Path('/home/workspace/logs')
SUPERVISOR_CONF = Path('/home/workspace/zo_sentinel/supervisord_sentinel_full.conf')
SERVICE_NAME = 'supervisor_auto_updater_v2'
PORT = None
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
POLL_SECS = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger(__name__)

_process_alive = False

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def get_db_path() -> str:
    return '/home/workspace/Datasets/zo-sentinel/duckdb/writeservice.db'

def check_single_instance() -> bool:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            if os.path.exists(f'/proc/{old_pid}'):
                LOG.warning(f'Another instance running as PID {old_pid}')
                return False
        except (ValueError, OSError):
            pass
        pid_path.unlink()
    pid_path.write_text(str(os.getpid()))
    return True

def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except OSError:
        pass

def signal_handler(signum: int, frame) -> None:
    global _process_alive
    sig_name = signal.Signals(signum).name
    LOG.info(f'Received {sig_name}, shutting down gracefully')
    _process_alive = False
    remove_pid_file()
    sys.exit(0)

def ws_query(sql: str) -> list:
    payload = {'sql': sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        LOG.error(f'ws_query failed: {e}')
        return []

def ws_write(table: str, rows: list) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        LOG.error(f'ws_write failed for table {table}: {e}')
        return False

def ws_execute(sql: str) -> bool:
    payload = {'sql': sql}
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        LOG.error(f'ws_execute failed: {e}')
        return False

def send_heartbeat(status: str = 'running', meta: Optional[dict] = None) -> None:
    rows = [{
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }]
    ws_write('service_health', rows)

def get_running_daemons() -> dict:
    sql = '''
        SELECT service, last_heartbeat 
        FROM service_health 
        WHERE last_heartbeat IS NOT NULL
    '''
    results = ws_query(sql)
    now = datetime.now(timezone.utc)
    running = {}
    for row in results:
        svc = row.get('service', '')
        hb = row.get('last_heartbeat', '')
        if not svc or not hb:
            continue
        try:
            hb_dt = datetime.fromisoformat(hb.replace('Z', '+00:00'))
            age = (now - hb_dt).total_seconds()
            if age < 300:
                running[svc] = hb
        except (ValueError, TypeError):
            pass
    return running

def get_daemon_configs_from_db() -> list:
    sql = '''
        SELECT 
            service_name,
            command,
            port,
            log_file,
            pid_file
        FROM mesh_memory.build_artifact
        WHERE interface = 'daemon'
        AND status = 'built'
        ORDER BY service_name
    '''
    return ws_query(sql)

def is_process_running(canonical_path: str) -> bool:
    import subprocess
    try:
        result = subprocess.run(
            ['pgrep', '-f', canonical_path],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                pid = pid.strip()
                if not pid:
                    continue
                try:
                    with open(f'/proc/{pid}/cmdline', 'r') as f:
                        cmdline = f.read()
                    if canonical_path in cmdline and '/home/workspace/logs/' not in cmdline:
                        return True
                except (OSError, IOError):
                    continue
        return False
    except Exception as e:
        LOG.debug(f'pgrep check failed: {e}')
        return False

def generate_supervisord_config(daemon_configs: list, running: dict) -> str:
    lines = [
        '[supervisord]',
        'nodaemon=true',
        'logfile=/home/workspace/logs/supervisord.log',
        'logfile_maxbytes=50MB',
        'logfile_backups=10',
        'loglevel=info',
        'pidfile=/tmp/supervisord.pid',
        'childlogdir=/home/workspace/logs',
        'user=root',
        '',
        '[rpcinterface:supervisor]',
        'supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface',
        '',
        '[supervisorctl]',
        'serverurl=http://127.0.0.1:9001',
        '',
    ]
    
    program_names = set()
    
    for cfg in daemon_configs:
        svc_name = cfg.get('service_name', '')
        command = cfg.get('command', '')
        log_file = cfg.get('log_file', f'/home/workspace/logs/{svc_name}.log')
        pid_file = cfg.get('pid_file', f'/tmp/{svc_name}.pid')
        
        if not svc_name or not command:
            continue
        
        base_name = svc_name.replace('.py', '').replace('-', '_')
        if base_name in program_names:
            counter = 1
            while f'{base_name}_{counter}' in program_names:
                counter += 1
            prog_name = f'{base_name}_{counter}'
        else:
            prog_name = base_name
        program_names.add(prog_name)
        
        canonical = command.split()[0] if command else ''
        running_state = 'started' if svc_name in running or is_process_running(canonical) else 'stopped'
        
        lines.extend([
            f'[program:{prog_name}]',
            f'command={command}',
            f'directory={PROJECT_DIR}',
            f'autostart={running_state == "started"}',
            f'autorestart=true',
            f'startretries=3',
            f'stderr_logfile={log_file}.err',
            f'stdout_logfile={log_file}',
            f'stdout_logfile_maxbytes=10MB',
            f'stderr_logfile_maxbytes=10MB',
            f'pidfile={pid_file}',
            f'user=root',
            '',
        ])
    
    return '\n'.join(lines)

def validate_config_syntax(config_content: str) -> tuple[bool, Optional[str]]:
    import configparser
    cp = configparser.ConfigParser()
    try:
        import io
        cp.read_string(config_content)
        return True, None
    except configparser.Error as e:
        return False, str(e)

def compute_config_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]

def read_current_config_hash() -> Optional[str]:
    hash_file = Path('/home/workspace/.supervisor_config_hash')
    if hash_file.exists():
        return hash_file.read_text().strip()
    return None

def write_config_hash(content: str) -> None:
    hash_file = Path('/home/workspace/.supervisor_config_hash')
    hash_file.write_text(compute_config_hash(content))

def write_supervisord_config(content: str) -> bool:
    try:
        SUPERVISOR_CONF.parent.mkdir(parents=True, exist_ok=True)
        backup = SUPERVISOR_CONF.with_suffix('.conf.backup')
        if SUPERVOR_CONF.exists():
            backup.write_text(SUPERVISOR_CONF.read_text())
        SUPERVISOR_CONF.write_text(content)
        write_config_hash(content)
        LOG.info(f'Wrote updated supervisord config to {SUPERVISOR_CONF}')
        return True
    except OSError as e:
        LOG.error(f'Failed to write config: {e}')
        return False

def reload_supervisord() -> bool:
    import subprocess
    try:
        result = subprocess.run(
            ['supervisorctl', 'reread'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            subprocess.run(
                ['supervisorctl', 'update'],
                capture_output=True,
                text=True,
                timeout=30
            )
            LOG.info('Supervisord reloaded successfully')
            return True
        LOG.warning(f'supervisorctl reread failed: {result.stderr}')
        return False
    except (subprocess.TimeoutExpired, OSError) as e:
        LOG.warning(f'supervisorctl reload failed: {e}')
        return False

def cycle() -> dict:
    result = {
        'daemons_found': 0,
        'config_changed': False,
        'error': None
    }
    
    daemon_configs = get_daemon_configs_from_db()
    result['daemons_found'] = len(daemon_configs)
    
    if not daemon_configs:
        LOG.info('No daemon configurations found in database')
        return result
    
    running = get_running_daemons()
    
    new_config = generate_supervisord_config(daemon_configs, running)
    
    valid, error = validate_config_syntax(new_config)
    if not valid:
        result['error'] = f'Config validation failed: {error}'
        LOG.error(result['error'])
        return result
    
    current_hash = read_current_config_hash()
    new_hash = compute_config_hash(new_config)
    
    if current_hash == new_hash:
        LOG.debug('Config unchanged, skipping write')
        return result
    
    if write_supervisord_config(new_config):
        result['config_changed'] = True
        reload_supervisord()
    else:
        result['error'] = 'Failed to write config file'
    
    return result

def run() -> None:
    global _process_alive
    
    if not check_single_instance():
        LOG.error('Another instance is running, exiting')
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    LOG.info(f'{SERVICE_NAME} starting')
    
    _process_alive = True
    
    while _process_alive:
        try:
            result = cycle()
            meta = {
                'daemons_found': result['daemons_found'],
                'config_changed': result['config_changed'],
                'error': result['error']
            }
            send_heartbeat(status='running', meta=meta)
        except Exception as e:
            LOG.exception(f'Cycle error: {e}')
            send_heartbeat(status='error', meta={'error': str(e)})
        
        time.sleep(POLL_SECS)
    
    remove_pid_file()

if __name__ == '__main__':
    run()