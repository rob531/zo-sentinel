import requests
import socket
import os
import logging
from concurrent.futures import ThreadPoolExecutor

def config_validator():
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Define constants
    write_service_url = 'http://127.0.0.1:8772'
    inference_router_url = 'http://127.0.0.1:8773'

    # Check write service health
    try:
        response = requests.post(write_service_url, json={'table': 'test_table', 'rows': {'column1': 'value1'}})
        if response.status_code != 200:
            logger.error('Write service not reachable')
            exit(1)
    except requests.RequestException as e:
        logger.error(f'Error checking write service: {e}')
        exit(1)

    # Check table existence
    try:
        response = requests.get(write_service_url + '/tables')
        if 'test_table' not in response.json():
            missing_tables = [table['name'] for table in response.json() if table['name'] != 'test_table']
            logger.error(f'Table(s) missing: {", ".join(missing_tables)}')
            print('Run schema.py to create tables.')
    except requests.RequestException as e:
        logger.error(f'Error checking table existence: {e}')

    # Check port availability
    try:
        socket.create_connection(('127.0.0.1', 8772))
        if not os.environ.get('write_service_port'):
            logger.error('Write service port not set')
            print('Set write_service_port environment variable.')
    except OSError as e:
        logger.error(f'Error checking write service port: {e}')

    try:
        socket.create_connection(('127.0.0.1', 8773))
        if not os.environ.get('inference_router_port'):
            logger.error('Inference router port not set')
            print('Set inference_router_port environment variable.')
    except OSError as e:
        logger.error(f'Error checking inference router port: {e}')

    # Check env vars
    required_env_vars = ['write_service_url', 'write_service_port']
    for var in required_env_vars:
        if not os.environ.get(var):
            logger.error(f'Environment variable {var} not set')
            print(f'Set environment variable {var}.')

def startup_checker():
    # Run config validator
    config_validator()

    # Check schema version
    try:
        response = requests.get(write_service_url + '/schema_version')
        if response.status_code != 200 or response.json() != '1':
            logger.error('Schema version mismatch')
            print('Run schema.py to update schema.')
    except requests.RequestException as e:
        logger.error(f'Error checking schema version: {e}')

def run():
    if __name__ == '__main__':
        startup_checker()