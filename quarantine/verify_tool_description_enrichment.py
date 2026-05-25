from fastapi import FastAPI, HTTPException
import requests
import logging
from typing import List
import json
import os
from datetime import datetime
import duckdb

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run():
    if __name__ == '__main__':
        app.run()

# ZO-SENTINEL Build State
ZO_SENTINEL_BUILD_STATE = {
    'threat_intel_ingestor': {
        'fns': ['run', 'cycle'],
        'consts': {
            'Sources': [
                {'query': 'world_articles', 'table': 'WHERE topics LIKE "%cybersecurity%" AND title LIKE "%mcp%"'},
            ]
        }
    }
}

# Successfully Built Files
ZO_SENTINEL_BUILT_FILES = {
    'verify_tool_description_safety_enrichment_effectiveness': {
        'built': '2026-04-30T02:52:05'
    },
    'threat_intel_ingestor.py': {
        'fns': ['run', 'cycle'],
        'consts': {
            'Sources': [
                {'query': 'world_articles', 'table': 'WHERE topics LIKE "%cybersecurity%" AND title LIKE "%mcp%"'},
            ]
        }
    },
}

# Verif Tool Description Safety Enrichment Effectiveness
def verify_tool_description_safety_enrichment_effectiveness():
    url = "http://127.0.0.1:8772/write"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        'table': 'tool_description_safety',
        'rows': [],
        'wait': True
    })

    try:
        response = requests.post(url, headers=headers, data=data)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to send POST request")
    except requests.RequestException as e:
        logger.error(e)
    else:
        logger.info("Successfully sent POST request")

def is_signal_quality_diagnostic_weak():
    url = "http://127.0.0.1:8772/write"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        'table': 'signal_quality',
        'rows': [
            {'signal': 'tool_description_safety', 'value': 3}
        ],
        'wait': True
    })

    try:
        response = requests.post(url, headers=headers, data=data)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to send POST request")
    except requests.RequestException as e:
        logger.error(e)
    else:
        logger.info("Successfully sent POST request")

def main():
    # Initialize logging
    logger.setLevel(logging.INFO)

    while True:
        verify_tool_description_safety_enrichment_effectiveness()
        is_signal_quality_diagnostic_weak()
        if True:  # Add logic to stop the loop when desired condition met
            break

    run()

if __name__ == '__main__':
    main()