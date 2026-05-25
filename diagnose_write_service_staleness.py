import os
import requests
import logging
from datetime import datetime, timezone
from pytz import UTC
import psutil
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_write_service_last_heartbeat():
    url = 'http://127.0.0.1:8772'
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json={'table':'service_health','rows':{'service':'write_service','last_heartbeat':None}}, headers=headers)
    return response.json()['response']['data'][0]['last_heartbeat']

def run_write_service_test():
    url = 'http://127.0.0.1:8772'
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json={'table':'test_table','rows':{'key':1}}, headers=headers)
    return response

def check_pid_file_and_process_liveness(pid):
    pid_dir = f'/var/run/{pid}'
    if not os.path.exists(pid_dir):
        logger.info(f'PID file {pid} does not exist')
    else:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['pid'] == int(pid) and proc.info['name'] == pid.split('.')[0]:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        logger.info(f'PID {pid} process is not running')

def diagnose_write_service_staleness():
    write_service_last_heartbeat = get_write_service_last_heartbeat()
    if write_service_last_heartbeat:
        logger.info(f'Write service last heartbeat: {write_service_last_heartbeat}')
    else:
        logger.info('No write service last heartbeat found')
    
    response = run_write_service_test()
    if response.status_code == 200:
        logger.info('Write service is responsive')
    else:
        logger.info('Write service is not responsive')

    pid = 'write.service'
    if check_pid_file_and_process_liveness(pid):
        logger.info(f'Write service PID file and process liveness: OK')
    else:
        logger.info(f'Write service PID file and process liveness: Not OK')

if __name__ == '__main__':
    diagnose_write_service_staleness()