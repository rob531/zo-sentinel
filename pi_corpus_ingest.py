import fcntl
import hashlib
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = 'pi_corpus_ingest'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_URL = 'http://127.0.0.1:8772/query'
QUARANTINE_BASE = '/home/workspace/zo_sentinel/pi_quarantine'
BORDAIR_URL = 'https://huggingface.co/datasets/Bordair/bordair-multimodal/resolve/main/payloads_v3/summary_v3.json'
AGENTDOJO_URL = 'https://huggingface.co/datasets/AgentDojo/agentdojo-function-calling/resolve/main/suites.jsonl'
ADDITIONAL_SOURCES = [
    ('gandalf', 'https://huggingface.co/datasets/Lakera/gandalf/resolve/main/prompts.json'),
    ('advbench', 'https://huggingface.co/datasets/llm-attacks/advbench/resolve/main/advbench_harmful.txt'),
]
CYCLE_SECS = 21600
HEARTBEAT_SECS = 60
FETCH_TIMEOUT = 30
MAX_RETRIES = 3
MAX_PAYLOADS_PER_SOURCE = 100
REQUEST_DELAY_SECS = 0.1
LOCK_FILE = '/tmp/pi_corpus_ingest.lock'
PID_FILE = '/tmp/pi_corpus_ingest.pid'
MAX_PROMPT_LEN = 10000
shutdown_flag = threading.Event()


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    print(f'[{SERVICE_NAME}] Received {sig_name}, initiating graceful shutdown...')
    shutdown_flag.set()


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        print(f'[{SERVICE_NAME}] Error removing PID file: {e}')


def check_single_instance():
    lock_file = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print(f'[{SERVICE_NAME}] Another instance is running. Exiting.')
        sys.exit(0)
    return lock_file


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(
            get_write_url(),
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        return resp.status_code == 200
    except Exception as e:
        print(f'[{SERVICE_NAME}] ws_write error: {e}')
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            get_query_url(),
            json={'sql': sql},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        return []
    except Exception as e:
        print(f'[{SERVICE_NAME}] ws_query error: {e}')
        return []


def send_heartbeat():
    while not shutdown_flag.is_set():
        try:
            requests.post(
                get_write_url(),
                json={
                    'table': 'service_health',
                    'rows': {
                        'service': SERVICE_NAME,
                        'last_heartbeat': datetime.now(timezone.utc).isoformat()
                    },
                    'wait': True
                },
                timeout=10
            )
        except Exception as e:
            print(f'[{SERVICE_NAME}] Heartbeat error: {e}')
        time.sleep(HEARTBEAT_SECS)


def fetch_with_retry(url: str, max_retries: int = MAX_RETRIES) -> Optional[str]:
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=FETCH_TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                print(f'[{SERVICE_NAME}] URL not found (404): {url}')
                return None
            else:
                print(f'[{SERVICE_NAME}] HTTP {resp.status_code} for {url}, attempt {attempt + 1}/{max_retries}')
        except requests.exceptions.Timeout:
            print(f'[{SERVICE_NAME}] Timeout for {url}, attempt {attempt + 1}/{max_retries}')
        except requests.exceptions.RequestException as e:
            print(f'[{SERVICE_NAME}] Request error for {url}: {e}, attempt {attempt + 1}/{max_retries}')
        if attempt < max_retries - 1:
            wait_time = (2 ** attempt) * 2
            time.sleep(wait_time)
    return None


def sha256_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def ensure_quarantine_dirs():
    os.makedirs(QUARANTINE_BASE, mode=0o755, exist_ok=True)
    for source in ['bordair', 'agentdojo', 'gandalf', 'advbench']:
        os.makedirs(os.path.join(QUARANTINE_BASE, source), mode=0o755, exist_ok=True)


def get_existing_hashes(source: str) -> set:
    source_dir = os.path.join(QUARANTINE_BASE, source)
    hashes = set()
    if os.path.isdir(source_dir):
        for fname in os.listdir(source_dir):
            if fname.endswith('.json'):
                hashes.add(fname.replace('.json', ''))
    return hashes


def count_total_quarantined() -> int:
    total = 0
    if os.path.isdir(QUARANTINE_BASE):
        for source in os.listdir(QUARANTINE_BASE):
            source_dir = os.path.join(QUARANTINE_BASE, source)
            if os.path.isdir(source_dir):
                total += len([f for f in os.listdir(source_dir) if f.endswith('.json')])
    return total


def quarantine_payload(source: str, source_dataset: str, source_url: str, payload: Dict[str, Any], existing_hashes: set) -> bool:
    try:
        prompt_text = payload.get('prompt_text', '') or payload.get('prompt', '') or payload.get('text', '') or ''
        if isinstance(prompt_text, list):
            prompt_text = ' '.join(str(p) for p in prompt_text)
        prompt_text = str(prompt_text)[:MAX_PROMPT_LEN]
        
        content_hash = sha256_hash(prompt_text)
        
        if content_hash in existing_hashes:
            return False
        
        injection_type = payload.get('injection_type', '') or payload.get('category', '') or payload.get('type', '') or 'unknown'
        target_domain = payload.get('target_domain', '') or payload.get('domain', '') or payload.get('target', '') or ''
        severity = payload.get('severity', '') or payload.get('harm_level', '') or payload.get('level', '') or 'unknown'
        is_multilingual = payload.get('is_multilingual', False) or payload.get('multilingual', False)
        language = payload.get('language', '') or payload.get('lang', '') or 'en'
        metadata = payload.get('metadata', {}) or payload.get('meta', {}) or {}
        
        quarantine_entry = {
            'content_hash': content_hash,
            'source_dataset': source_dataset,
            'source_url': source_url,
            'prompt_text': prompt_text,
            'injection_type': str(injection_type),
            'target_domain': str(target_domain),
            'severity': str(severity),
            'is_multilingual': bool(is_multilingual),
            'language': str(language),
            'metadata': metadata if isinstance(metadata, dict) else {},
            'ingested_at': datetime.now(timezone.utc).isoformat(),
            'triaged': False,
            'triage_note': ''
        }
        
        target_dir = os.path.join(QUARANTINE_BASE, source)
        out_path = os.path.join(target_dir, f'{content_hash}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(quarantine_entry, f, ensure_ascii=False, indent=2)
        
        existing_hashes.add(content_hash)
        return True
    except Exception as e:
        print(f'[{SERVICE_NAME}] Error quarantining payload: {e}')
        return False


def ingest_bordair(existing_hashes: set, stats: Dict[str, int]) -> int:
    content = fetch_with_retry(BORDAIR_URL)
    if not content:
        return 0
    
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        print(f'[{SERVICE_NAME}] Failed to parse Bordair JSON: {e}')
        return 0
    
    count = 0
    payloads = []
    
    if isinstance(data, list):
        payloads = data
    elif isinstance(data, dict):
        if 'payloads' in data:
            payloads = data['payloads']
        elif 'data' in data:
            payloads = data['data']
        else:
            payloads = [data]
    
    for i, payload in enumerate(payloads[:MAX_PAYLOADS_PER_SOURCE]):
        if shutdown_flag.is_set():
            break
        if quarantine_payload('bordair', 'Bordair/bordair-multimodal', BORDAIR_URL, payload, existing_hashes):
            count += 1
        if i < len(payloads) - 1:
            time.sleep(REQUEST_DELAY_SECS)
    
    stats['bordair'] = count
    return count


def ingest_agentdojo(existing_hashes: set, stats: Dict[str, int]) -> int:
    content = fetch_with_retry(AGENTDOJO_URL)
    if not content:
        return 0
    
    count = 0
    lines = content.strip().split('\n')
    
    for i, line in enumerate(lines[:MAX_PAYLOADS_PER_SOURCE]):
        if shutdown_flag.is_set():
            break
        try:
            payload = json.loads(line)
            if quarantine_payload('agentdojo', 'AgentDojo/agentdojo-function-calling', AGENTDOJO_URL, payload, existing_hashes):
                count += 1
        except json.JSONDecodeError:
            pass
        if i < len(lines) - 1:
            time.sleep(REQUEST_DELAY_SECS)
    
    stats['agentdojo'] = count
    return count


def ingest_additional_sources(existing_hashes: set, stats: Dict[str, int]) -> int:
    total = 0
    for source, url in ADDITIONAL_SOURCES:
        if shutdown_flag.is_set():
            break
        
        content = fetch_with_retry(url)
        if not content:
            continue
        
        source_count = 0
        
        if url.endswith('.json'):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    items = data
                else:
                    items = [data]
                for i, item in enumerate(items[:MAX_PAYLOADS_PER_SOURCE]):
                    if shutdown_flag.is_set():
                        break
                    entry = {'prompt_text': item.get('prompt', '') or item.get('text', '') or item}
                    if quarantine_payload(source, f'Lakera/{source}', url, entry, existing_hashes):
                        source_count += 1
                    if i < len(items) - 1:
                        time.sleep(REQUEST_DELAY_SECS)
            except json.JSONDecodeError as e:
                print(f'[{SERVICE_NAME}] Failed to parse {source} JSON: {e}')
        elif url.endswith('.txt'):
            lines = content.strip().split('\n')
            for i, line in enumerate(lines[:MAX_PAYLOADS_PER_SOURCE]):
                if shutdown_flag.is_set():
                    break
                entry = {'prompt_text': line}
                if quarantine_payload(source, 'llm-attacks/advbench', url, entry, existing_hashes):
                    source_count += 1
                if i < len(lines) - 1:
                    time.sleep(REQUEST_DELAY_SECS)
        
        stats[source] = source_count
        total += source_count
    
    return total


def write_stats_to_mesh(ingested_this_cycle: int, total_quarantined: int, by_source_counts: Dict[str, int]):
    try:
        mesh_entry = {
            'event_type': 'pi_corpus_ingest_cycle',
            'source': SERVICE_NAME,
            'ingested_this_cycle': ingested_this_cycle,
            'total_quarantined': total_quarantined,
            'by_source_counts': json.dumps(by_source_counts),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        ws_write('mesh_memory', mesh_entry)
    except Exception as e:
        print(f'[{SERVICE_NAME}] Failed to write stats to mesh_memory: {e}')


def run():
    ensure_quarantine_dirs()
    
    lock_file = check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    heartbeat_thread = threading.Thread(target=send_heartbeat, daemon=True)
    heartbeat_thread.start()
    
    print(f'[{SERVICE_NAME}] Starting Pi Corpus Ingest Daemon')
    print(f'[{SERVICE_NAME}] Quarantine path: {QUARANTINE_BASE}')
    
    try:
        while not shutdown_flag.is_set():
            cycle_start = time.time()
            
            existing_hashes = {}
            for source in ['bordair', 'agentdojo', 'gandalf', 'advbench']:
                existing_hashes[source] = get_existing_hashes(source)
            
            stats = {
                'bordair': 0,
                'agentdojo': 0,
                'gandalf': 0,
                'advbench': 0
            }
            
            ingested_this_cycle = 0
            
            print(f'[{SERVICE_NAME}] Starting ingestion cycle...')
            
            ingested_this_cycle += ingest_bordair(existing_hashes['bordair'], stats)
            if shutdown_flag.is_set():
                break
            
            ingested_this_cycle += ingest_agentdojo(existing_hashes['agentdojo'], stats)
            if shutdown_flag.is_set():
                break
            
            ingested_this_cycle += ingest_additional_sources(existing_hashes, stats)
            
            total_quarantined = count_total_quarantined()
            
            print(f'[{SERVICE_NAME}] Cycle complete: {ingested_this_cycle} new payloads quarantined')
            print(f'[{SERVICE_NAME}] Total quarantined: {total_quarantined}')
            print(f'[{SERVICE_NAME}] By source: {stats}')
            
            write_stats_to_mesh(ingested_this_cycle, total_quarantined, stats)
            
            cycle_duration = time.time() - cycle_start
            print(f'[{SERVICE_NAME}] Cycle took {cycle_duration:.1f}s, sleeping for {CYCLE_SECS}s...')
            
            for _ in range(CYCLE_SECS):
                if shutdown_flag.is_set():
                    break
                time.sleep(1)
    
    except Exception as e:
        print(f'[{SERVICE_NAME}] Fatal error: {e}')
    finally:
        remove_pid_file()
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        print(f'[{SERVICE_NAME}] Shutdown complete')


if __name__ == '__main__':
    run()