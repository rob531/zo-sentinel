import requests
from fastapi import FastAPI, Request
import logging
from datetime import datetime
import json
import time
from typing import Optional

logging.basicConfig(level=logging.INFO)

app = FastAPI()

def diagnose_ws_staleness(
    threshold: int = 300,
    write_service_url: str = "http://127.0.0.1:8772",
) -> dict:
    diagnostic_output = {
        "timestamp": datetime.now().isoformat(),
        "service_health_query": {},
        "write_service_query": {},
        "findings": [],
    }

    service_health_query = {"table": "service_health", "rows": {"service": "write_service", "last_heartbeat": None}}
    try:
        response = requests.post(
            write_service_url + "/write",
            json=service_health_query,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 200:
            diagnostic_output["service_health_query"]["result"] = True
        else:
            diagnostic_output["service_health_query"]["result"] = False
    except requests.exceptions.RequestException as e:
        diagnostic_output["service_health_query"]["error"] = str(e)

    write_service_query = {"table": "write_service", "rows": {"last_write_time": None}}
    try:
        response = requests.post(write_service_url, json=write_service_query)
        if response.status_code == 200:
            diagnostic_output["write_service_query"]["result"] = True
            diagnostic_output["write_service_query"]["last_write_time"] = datetime.now().isoformat()
        else:
            diagnostic_output["write_service_query"]["result"] = False
    except requests.exceptions.RequestException as e:
        diagnostic_output["write_service_query"]["error"] = str(e)

    if (
        time.time() - int(diagnostic_output["write_service_query"]["last_write_time"]) > threshold
    ):
        diagnostic_output["findings"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "issue_type": "staleness",
                "description": f"Write service is stale at {int(time.time() - int(diagnostic_output['write_service_query']['last_write_time']))} seconds",
            }
        )
    else:
        diagnostic_output["findings"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "issue_type": "healthy",
                "description": "Write service is healthy and up-to-date",
            }
        )

    return diagnostic_output

@app.post("/diagnose/ws-staleness")
async def diagnose_ws_staleness_endpoint():
    data = await Request.json()
    return diagnose_ws_staleness(**data)