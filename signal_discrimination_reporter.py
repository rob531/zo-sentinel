import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import requests
import logging
import os
import sys
import fastapi
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from fastapi import Request
from pydantic import BaseModel

app = fastapi.FastAPI()

class DiagnosticRequest(BaseModel):
    signal_type: str
    distinct_scores: int

def run() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Query signal scores and enrichments
    query_url = WRITE_SERVICE_URL + "/query"
    response = requests.post(query_url, json={'table': 'mcp_signal_scores', 'rows': {}})

    if not response.status_code == 200:
        raise HTTPException(status_code=400, detail='Failed to retrieve signal scores')

    signal_scores_data = response.json()
    query_url = WRITE_SERVICE_URL + "/query"
    response = requests.post(query_url, json={'table': 'mcp_signal_enrichments', 'rows': {}})

    if not response.status_code == 200:
        raise HTTPException(status_code=400, detail='Failed to retrieve signal enrichments')

    signal_enrichments_data = response.json()

    # Compute distinct score counts per signal_type
    logger.info('Computing distinct score counts')
    distinct_scores_count = {}
    for row in signal_scores_data['rows']:
        if row['signal_type'] not in distinct_scores_count:
            distinct_scores_count[row['signal_type']] = set()
        distinct_scores_count[row['signal_type']].add(row['score'])

    # Flag any signal with <10 distinct scores as WEAK
    logger.info('Flagging weak signals')
    weak_signals = []
    for row in signal_scores_data['rows']:
        if len(distinct_scores_count[row['signal_type']]) < 10:
            weak_signals.append(row)

    # Write diagnostic row to service_health or dedicated diagnostics table
    logger.info('Writing diagnostic rows')
    diagnose_url = 'http://127.0.0.1:8793/write'
    data = {'table': 'service_health', 'rows': [{'signal_type': signal['signal_type'], 'distinct_scores': len(distinct_scores_count[signal['signal_type']])} for signal in weak_signals]}

    if not requests.post(diagnose_url, json=data).status_code == 200:
        raise HTTPException(status_code=400, detail='Failed to write diagnostic rows')

def cycle() -> None:
    while True:
        run()
        time.sleep(60)