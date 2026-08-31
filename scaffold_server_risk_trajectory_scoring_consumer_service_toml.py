import os
import logging
from pathlib import Path

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename=str(LOG_DIR / 'scaffold_server_risk_trajectory_scoring_consumer_service_toml.log'),
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'scaffold_server_risk_trajectory_scoring_consumer_service_toml'
SERVICE_DIR = Path('/home/workspace/zo_sentinel')
LOG_FILE = LOG_DIR / f'{SERVICE_NAME}.log'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'


def write_service_toml() -> str:
    toml_path = SERVICE_DIR / 'server_risk_trajectory_scoring_consumer_service.toml'
    toml_content = """[service]
name = "server_risk_trajectory_scoring_consumer"
module = "server_risk_trajectory_scoring_consumer_init"
port = 8796
log = "/home/workspace/logs/server_risk_trajectory_scoring_consumer.log"
pid_file = "/tmp/server_risk_trajectory_scoring_consumer.pid"

[service.run]
command = "python3 -m uvicorn server_risk_trajectory_scoring_consumer_init:app --host 0.0.0.0 --port 8796"
auto_start = true
autorestart = true
redirect_stderr = true
stdout_logfile = "/home/workspace/logs/server_risk_trajectory_scoring_consumer_daemon.log"
stdout_logfile_maxbytes = "10MB"
stdout_logfile_backups = 3
environment = "PYTHONPATH=/home/workspace/zo_sentinel"

[write_service]
url = "http://localhost:8772"

[supervisord]
priority = 850
"""
    return toml_content


def write_toml_file(toml_content: str) -> bool:
    toml_path = SERVICE_DIR / 'server_risk_trajectory_scoring_consumer_service.toml'
    try:
        with open(toml_path, 'w', encoding='utf-8') as fh:
            fh.write(toml_content)
        log.info('Wrote service TOML: %s', toml_path)
        return True
    except Exception as e:
        log.error('Failed to write TOML: %s', e)
        return False


def main():
    log.info('Generating server_risk_trajectory_scoring_consumer_service.toml')
    toml_content = write_service_toml()
    success = write_toml_file(toml_content)
    if success:
        log.info('Done. TOML written successfully.')
        print(f'Service TOML written to: {SERVICE_DIR / "server_risk_trajectory_scoring_consumer_service.toml"}')
    else:
        log.error('Failed to write TOML.')
    return success


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)