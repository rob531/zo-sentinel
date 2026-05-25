import os
import re
import json
import time
import signal
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = 'pi_flagged_review_api'
FLAGGED_DIR = '/home/workspace/zo_sentinel/pi_review/flagged'
REJECTED_DIR = '/home/workspace/zo_sentinel/pi_review/rejected'
APPROVED_DIR = '/home/workspace/zo_sentinel/pi_review/approved'
REVIEW_LOG = '/home/workspace/zo_sentinel/pi_review/log.jsonl'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
PORT = 8792
HEARTBEAT_SECS = 60

app = FastAPI(title=SERVICE_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

HASH_PATTERN = re.compile(r'^[a-fA-F0-9]{64}$')

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False

def heartbeat_loop():
    while True:
        ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()})
        time.sleep(HEARTBEAT_SECS)

def validate_hash(hash_str: str) -> bool:
    return bool(HASH_PATTERN.match(hash_str))

def load_json_file(filepath: str) -> Optional[Dict[str, Any]]:
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'[{SERVICE_NAME}] Error loading {filepath}: {e}')
        return None

def count_files_in_dir(directory: str) -> int:
    try:
        return len([f for f in os.listdir(directory) if f.endswith('.json')])
    except FileNotFoundError:
        return 0

def get_flagged_files(limit: int = 50, offset: int = 0, category: Optional[str] = None) -> List[Dict[str, Any]]:
    results = []
    try:
        files = [f for f in os.listdir(FLAGGED_DIR) if f.endswith('.json')]
    except FileNotFoundError:
        return results
    
    for filename in files:
        filepath = os.path.join(FLAGGED_DIR, filename)
        data = load_json_file(filepath)
        if data is None:
            continue
        
        if category:
            categories = data.get('review', {}).get('categories', [])
            if category not in categories:
                continue
        
        prompt_text = data.get('prompt_text', '')
        results.append({
            'hash': filename.replace('.json', ''),
            'source': data.get('source', ''),
            'prompt_text_preview': prompt_text[:200] if prompt_text else '',
            'review': data.get('review', {}),
            'ingested_at': data.get('ingested_at', ''),
            'flagged_at': data.get('flagged_at', data.get('ingested_at', ''))
        })
    
    results.sort(key=lambda x: x['flagged_at'], reverse=True)
    return results[offset:offset + limit]

def get_full_flagged_payload(hash_str: str) -> Optional[Dict[str, Any]]:
    if not validate_hash(hash_str):
        return None
    
    filepath = os.path.join(FLAGGED_DIR, f'{hash_str}.json')
    return load_json_file(filepath)

def get_stats() -> Dict[str, Any]:
    category_counts = {}
    source_counts = {}
    confidence_buckets = {'0.0-0.25': 0, '0.25-0.5': 0, '0.5-0.75': 0, '0.75-1.0': 0}
    
    for directory in [FLAGGED_DIR, APPROVED_DIR, REJECTED_DIR]:
        try:
            files = [f for f in os.listdir(directory) if f.endswith('.json')]
        except FileNotFoundError:
            continue
        
        for filename in files:
            filepath = os.path.join(directory, filename)
            data = load_json_file(filepath)
            if data is None:
                continue
            
            source = data.get('source', 'unknown')
            source_counts[source] = source_counts.get(source, 0) + 1
            
            for cat in data.get('review', {}).get('categories', []):
                category_counts[cat] = category_counts.get(cat, 0) + 1
            
            confidence = data.get('review', {}).get('confidence', 0)
            if confidence < 0.25:
                confidence_buckets['0.0-0.25'] += 1
            elif confidence < 0.5:
                confidence_buckets['0.25-0.5'] += 1
            elif confidence < 0.75:
                confidence_buckets['0.5-0.75'] += 1
            else:
                confidence_buckets['0.75-1.0'] += 1
    
    return {
        'category_counts': category_counts,
        'source_counts': source_counts,
        'confidence_buckets': confidence_buckets,
        'total_flagged': count_files_in_dir(FLAGGED_DIR),
        'total_approved': count_files_in_dir(APPROVED_DIR),
        'total_rejected': count_files_in_dir(REJECTED_DIR)
    }

def get_reasoning(hash_str: str) -> Optional[Dict[str, Any]]:
    if not validate_hash(hash_str):
        return None
    
    filepath = os.path.join(FLAGGED_DIR, f'{hash_str}.json')
    data = load_json_file(filepath)
    if data is None:
        return None
    return data.get('review', {})

def get_log_tail(lines: int = 100) -> List[Dict[str, Any]]:
    try:
        if not os.path.exists(REVIEW_LOG):
            return []
        
        with open(REVIEW_LOG, 'r') as f:
            all_lines = f.readlines()
        
        tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        results = []
        for line in tail_lines:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results
    except Exception as e:
        print(f'[{SERVICE_NAME}] Error reading log: {e}')
        return []

def perform_override(hash_str: str, decision: str, reason: str, by: str) -> Dict[str, Any]:
    if not validate_hash(hash_str):
        raise HTTPException(status_code=400, detail='Invalid hash format. Must be 64-character hex.')
    
    if decision not in ('APPROVE', 'REJECT'):
        raise HTTPException(status_code=400, detail='Decision must be APPROVE or REJECT.')
    
    if len(reason) < 1 or len(reason) > 500:
        raise HTTPException(status_code=400, detail='Reason must be 1-500 characters.')
    
    source_file = os.path.join(FLAGGED_DIR, f'{hash_str}.json')
    if not os.path.exists(source_file):
        raise HTTPException(status_code=404, detail=f'Flagged payload {hash_str} not found.')
    
    dest_dir = APPROVED_DIR if decision == 'APPROVE' else REJECTED_DIR
    dest_file = os.path.join(dest_dir, f'{hash_str}.json')
    
    try:
        data = load_json_file(source_file)
        if data is None:
            raise HTTPException(status_code=500, detail='Failed to read flagged payload.')
        
        with open(dest_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        os.remove(source_file)
        
        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'action': 'manual_override',
            'hash': hash_str,
            'previous_state': 'flagged',
            'new_state': decision.lower(),
            'reason': reason,
            'by': by
        }
        
        os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
        with open(REVIEW_LOG, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
        
        return {
            'ok': True,
            'hash': hash_str,
            'moved_to': dest_dir,
            'logged': True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Override failed: {str(e)}')

@app.get('/health')
def health():
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'flagged_count': count_files_in_dir(FLAGGED_DIR),
        'approved_count': count_files_in_dir(APPROVED_DIR),
        'rejected_count': count_files_in_dir(REJECTED_DIR)
    }

@app.get('/flagged')
def list_flagged(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    category: Optional[str] = None
):
    return get_flagged_files(limit=limit, offset=offset, category=category)

@app.get('/flagged/{hash_str}')
def get_flagged_detail(hash_str: str):
    if not validate_hash(hash_str):
        raise HTTPException(status_code=400, detail='Invalid hash format. Must be 64-character hex.')
    
    data = get_full_flagged_payload(hash_str)
    if data is None:
        raise HTTPException(status_code=404, detail=f'Flagged payload {hash_str} not found.')
    
    return data

@app.get('/stats')
def stats():
    return get_stats()

@app.get('/reasoning/{hash_str}')
def get_reasoning_endpoint(hash_str: str):
    if not validate_hash(hash_str):
        raise HTTPException(status_code=400, detail='Invalid hash format. Must be 64-character hex.')
    
    reasoning = get_reasoning(hash_str)
    if reasoning is None:
        raise HTTPException(status_code=404, detail=f'Flagged payload {hash_str} not found.')
    
    return reasoning

@app.get('/log/tail')
def log_tail(lines: int = Query(default=100, ge=1, le=10000)):
    return get_log_tail(lines=lines)

@app.post('/override/{hash_str}')
def override(hash_str: str, body: Dict[str, Any]):
    decision = body.get('decision', '')
    reason = body.get('reason', '')
    by = body.get('by', 'unknown')
    
    if not decision:
        raise HTTPException(status_code=400, detail='Decision is required.')
    if not reason:
        raise HTTPException(status_code=400, detail='Reason is required.')
    
    return perform_override(hash_str, decision, reason, by)

def run():
    print(f'[{SERVICE_NAME}] Starting...')
    
    os.makedirs(FLAGGED_DIR, exist_ok=True)
    os.makedirs(REJECTED_DIR, exist_ok=True)
    os.makedirs(APPROVED_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(REVIEW_LOG), exist_ok=True)
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    print(f'[{SERVICE_NAME}] Heartbeat started, starting uvicorn on port {PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')

if __name__ == '__main__':
    run()