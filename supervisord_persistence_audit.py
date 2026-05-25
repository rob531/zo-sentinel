import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Set, List, Dict

import psutil
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
SUPERVISORD_CONFIG = "/etc/zo/supervisord-user.conf"
CHECK_INTERVAL = 1800  # 30 minutes
WORKSPACE_PATH = "/home/workspace/"


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("supervisord_persistence_audit")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    return logger


def parse_supervisord_config(config_path: str, logger: logging.Logger) -> Set[str]:
    registered_programs: Set[str] = set()
    try:
        with open(config_path, 'r') as f:
            content = f.read()
        pattern = r'^\[program:([^\]]+)\]'
        for line in content.split('\n'):
            match = re.match(pattern, line.strip())
            if match:
                registered_programs.add(match.group(1))
        logger.info(f"Parsed {len(registered_programs)} registered programs from supervisord config")
    except FileNotFoundError:
        logger.warning(f"Supervisord config not found: {config_path}")
    except Exception as e:
        logger.error(f"Error parsing supervisord config: {e}")
    return registered_programs


def find_workspace_python_daemons(logger: logging.Logger) -> List[Dict]:
    daemons: List[Dict] = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            if proc.info['name'] not in ('python', 'python3', 'python3.11'):
                continue
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline)
            if WORKSPACE_PATH in cmdline_str:
                daemon_info = {
                    'pid': proc.info['pid'],
                    'cmdline': cmdline_str,
                    'start_time': datetime.fromtimestamp(proc.info['create_time'], tz=timezone.utc).isoformat()
                }
                daemons.append(daemon_info)
                logger.info(f"Found workspace Python daemon: PID {proc.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return daemons


def extract_program_name_from_cmdline(cmdline: str) -> str:
    match = re.search(r'/home/workspace/[^/]+/(\w+)\.py', cmdline)
    if match:
        return match.group(1)
    match = re.search(r'/home/workspace/[^/]+/(\w+)_daemon\.py', cmdline)
    if match:
        return match.group(1)
    return ""


def audit(logger: logging.Logger) -> List[Dict]:
    unregistered: List[Dict] = []
    registered_programs = parse_supervisord_config(SUPERVISORD_CONFIG, logger)
    workspace_daemons = find_workspace_python_daemons(logger)
    logger.info(f"Checking {len(workspace_daemons)} workspace daemons against {len(registered_programs)} registered programs")
    for daemon in workspace_daemons:
        program_name = extract_program_name_from_cmdline(daemon['cmdline'])
        is_registered = program_name in registered_programs if program_name else False
        if not is_registered:
            logger.warning(f"Unregistered daemon detected: {program_name} (PID {daemon['pid']})")
            unregistered.append({
                'program_name': program_name or 'unknown',
                'pid': daemon['pid'],
                'cmdline': daemon['cmdline'],
                'start_time': daemon['start_time'],
                'should_be_registered': program_name in registered_programs
            })
    return unregistered


def report_unregistered(unregistered: List[Dict], logger: logging.Logger) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    if not unregistered:
        payload = {
            'table': 'supervisord_persistence_audit',
            'rows': {
                'service': 'supervisord_persistence_audit',
                'last_heartbeat': timestamp,
                'status': 'clean',
                'unregistered_count': 0,
                'message': 'No unregistered daemons detected'
            },
            'wait': True
        }
    else:
        audit_log = {
            'service': 'supervisord_persistence_audit',
            'last_heartbeat': timestamp,
            'status': 'alert',
            'unregistered_count': len(unregistered),
            'unregistered_daemons': unregistered,
            'message': f'Found {len(unregistered)} unregistered daemons'
        }
        payload = {
            'table': 'supervisord_persistence_audit',
            'rows': audit_log,
            'wait': True
        }
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Audit report sent: {len(unregistered)} unregistered daemons")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send audit report: {e}")


def run(logger: logging.Logger) -> None:
    logger.info("Starting Supervisord Persistence Audit Daemon")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info(f"Config: {SUPERVISORD_CONFIG}")
    while True:
        try:
            unregistered = audit(logger)
            report_unregistered(unregistered, logger)
        except Exception as e:
            logger.error(f"Audit cycle failed: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    logger = setup_logging()
    run(logger)