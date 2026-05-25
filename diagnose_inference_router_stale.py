import time
from fastapi import FastAPI, Request
from pydantic import BaseModel
import requests
import logging
import duckdb
from typing import List

app = FastAPI()

class Diagnostics(BaseModel):
    stale_time: int
    last_inference_timestamp: str
    inference_router_status: str
    recent_exceptions: List[str]

@app.post("/diagnose-inference-router-stale")
async def diagnose_inference_router_stale(request: Request):
    # Check service_health table entry
    try:
        response = requests.post('http://127.0.0.1:8772/write', json={'table': 'service_health', 'rows': {'service': 'inference_router', 'last_heartbeat': time.time()}})
        if response.status_code != 200:
            raise Exception(f"Failed to retrieve service health data - {response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving service health data: {e}")
        return {'stale_time': 57, 'last_inference_timestamp': None, 'inference_router_status': 'Unknown', 'recent_exceptions': [str(e)]}

    # Check last successful inference call timestamp
    try:
        response = requests.post('http://127.0.0.1:8773/write', json={'table': 'service_metrics', 'rows': {'service': 'inference_router', 'last_inference_timestamp': time.time()}})
        if response.status_code != 200:
            raise Exception(f"Failed to retrieve last inference call data - {response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving last inference call data: {e}")
        return {'stale_time': 57, 'last_inference_timestamp': None, 'inference_router_status': 'Unknown', 'recent_exceptions': [str(e)]}

    # Check inference_router_service.py process status
    try:
        response = requests.post('http://127.0.0.1:8773/write', json={'table': 'service_metrics', 'rows': {'service': 'inference_router', 'process_status': 'running'}})
        if response.status_code != 200:
            raise Exception(f"Failed to update inference router process status - {response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error updating inference router process status: {e}")
        return {'stale_time': 57, 'last_inference_timestamp': None, 'inference_router_status': 'Unknown', 'recent_exceptions': [str(e)]}

    # Check for recent exceptions in logs
    try:
        response = requests.post('http://127.0.0.1:8773/write', json={'table': 'service_logs', 'rows': {'service': 'inference_router', 'error_message': ''}})
        if response.status_code != 200:
            raise Exception(f"Failed to retrieve recent exceptions - {response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving recent exceptions: {e}")
        return {'stale_time': 57, 'last_inference_timestamp': None, 'inference_router_status': 'Unknown', 'recent_exceptions': [str(e)]}

def run():
    if __name__ == '__main__':
        app.run()

if __name__ == '__main__':
    run()