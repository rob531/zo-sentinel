import time
from typing import List
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ApiHealthChecker:
    def __init__(self):
        self.api_ports: List[int] = [8780, 8781, 8782, 8783, 8784, 8785, 8786, 8790, 8795]

    async def ping_api_port(self, port: int):
        try:
            await requests.post(f"http://127.0.0.1:{port}/health", json={"service": "health"})
            logger.info(f"API port {port} is up")
        except Exception as e:
            logger.error(f"API port {port} is down")

    async def check_api_health(self):
        for port in self.api_ports:
            await self.ping_api_port(port)

async def run():
    api_health_checker = ApiHealthChecker()
    while True:
        await api_health_checker.check_api_health()
        time.sleep(300)  # every 5 minutes

if __name__ == "__main__":
    import asyncio
    asyncio.run(run())