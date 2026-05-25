import requests
from fastapi import FastAPI
from logging import getLogger, debug, exception
from time import time
import os

app = FastAPI()

logger = getLogger(__name__)

async def query_write_service_health(url):
    response = await requests.post(url + '/health', json={'wait': True})
    return response.json()

def is_process_lively():
    try:
        return os.kill(8772, 0) == 0
    except OSError as e:
        logger.error(f'Failed to check write_service process liveness: {e}')
        return False

async def diagnose_write_service_stale_heartbeat(url):
    health = await query_write_service_health(url)
    if 'healthy' not in health or health['healthy'] is False:
        logger.warning('Write service appears down, checking process liveness')
        if is_process_lively():
            logger.warning('Process seems to be alive, updating meta with stale heartbeat')
            # Update write_service meta with current time
            pass
        else:
            logger.error('Write service process appears dead')
    else:
        logger.info('Write service appears healthy')

async def log_findings(service_name):
    debug(f'The write service appeared stale at {time()}: {service_name}')
    response = requests.post(url='/write', json={'table': 'service_health', 'rows': {'service': service_name, 'last_heartbeat': time()}})
    if not response.ok:
        logger.error('Failed to log findings')
    else:
        debug(f'Log entry created for stale write service: {response.json()}')

def main():
    import asyncio
    url = 'http://127.0.0.1:8772'
    loop = asyncio.get_event_loop()
    loop.create_task(diagnose_write_service_stale_heartbeat(url))
    loop.create_task(log_findings('write_service'))
    if __name__ == '__main__':
        run()

def run():
    from zo_sentinel import sources
    query_world_articles(sources)
    main()