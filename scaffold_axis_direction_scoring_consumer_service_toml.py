import os
import sys
from pathlib import Path

SERVICE_NAME = 'axis_direction_scoring_consumer'
SERVICE_DIR = Path('/home/workspace/zo_sentinel')
LOGS_DIR = Path('/home/workspace/logs')
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / f'{SERVICE_NAME}.log'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

TOML_CONTENT = f'''[unit]
Name={SERVICE_NAME}
Description=Axis Direction Scoring Consumer for Zo Sentinel
After=network.target write-service.target

[service]
Type=simple
User=workspace
WorkingDirectory={SERVICE_DIR}
Environment="PYTHONPATH={SERVICE_DIR}:$PYTHONPATH"
Environment="WRITE_SERVICE_URL=http://localhost:8772"
ExecStart=/home/workspace/venvs/zo-sentinel/bin/python -m zo_sentinel.axis_direction_scoring_consumer
Restart=on-failure
RestartSec=10
StandardOutput=append:{LOG_FILE}
StandardError=append:{LOG_FILE}

[install]
WantedBy=multi-user.target
'''

OUTPUT_PATH = SERVICE_DIR / f'{SERVICE_NAME}_service.toml'

def write_service_toml():
    with open(OUTPUT_PATH, 'w') as f:
        f.write(TOML_CONTENT.strip())
    print(f'Wrote service TOML to {OUTPUT_PATH}')
    return OUTPUT_PATH

def main():
    path = write_service_toml()
    print(f'Generated: {path}')
    print(f'Service name: {SERVICE_NAME}')
    print(f'PID file: {PID_FILE}')
    print(f'Log file: {LOG_FILE}')
    sys.exit(0)

if __name__ == '__main__':
    main()