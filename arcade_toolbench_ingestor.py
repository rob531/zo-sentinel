import sys
sys.path.insert(0, '/home/workspace/zo_mesh')
from zo_services import DataService
import requests
import time
import hashlib
import json
from bs4 import BeautifulSoup
from datetime import datetime

SERVICE_NAME = "arcade_toolbench_ingestor"
FETCH_TIMEOUT = 10
FETCH_DELAY_SUCCESS = 3
FETCH_DELAY_ERROR = 1
BATCH_SIZE = 50

def ensure_tables(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS third_party_benchmarks (
            server_id VARCHAR,
            source VARCHAR,
            security_grade VARCHAR,
            compliance_grade VARCHAR,
            quality_grade VARCHAR,
            raw_html_hash VARCHAR,
            error VARCHAR,
            fetched_at TIMESTAMPTZ DEFAULT now()
        )
    """)

def fetch_toolbench_grades(server_name):
    try:
        url = f'https://arcade.dev/toolbench/search?q={server_name}'
        headers = {
            'User-Agent': 'ZO-SENTINEL-BOT/1.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        response = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        security_grade_elem = soup.find(class_='security-grade')
        security_grade = security_grade_elem.text.strip() if security_grade_elem else 'N/A'
        
        compliance_grade_elem = soup.find(class_='compliance-grade')
        compliance_grade = compliance_grade_elem.text.strip() if compliance_grade_elem else 'N/A'
        
        quality_grade_elem = soup.find(class_='quality-grade')
        quality_grade = quality_grade_elem.text.strip() if quality_grade_elem else 'N/A'
        
        raw_html_hash = hashlib.md5(response.text.encode()).hexdigest()
        
        return {
            'security_grade': security_grade,
            'compliance_grade': compliance_grade,
            'quality_grade': quality_grade,
            'raw_html_hash': raw_html_hash,
            'error': None
        }
    except Exception as e:
        return {
            'security_grade': 'ERROR',
            'compliance_grade': 'ERROR',
            'quality_grade': 'ERROR',
            'raw_html_hash': None,
            'error': str(e)[:200]
        }

def ws_write(db, table, rows):
    db.write(table, rows)

def ws_query(db, query, params=None):
    return db.read(query, params) if params else db.read(query)

def run():
    db = DataService('t1.arcade_toolbench_ingestor')
    start = time.monotonic()
    
    try:
        ensure_tables(db)
        
        rows = ws_query(db, "SELECT server_id, name, url FROM mcp_server_registry WHERE toolbench_grade IS NULL LIMIT " + str(BATCH_SIZE))
        
        if not rows:
            db.log_run(status='ok', duration_ms=int((time.monotonic() - start) * 1000), info='no_servers_to_process')
            return
        
        processed = 0
        errors = 0
        
        for row in rows:
            server_id = row['server_id']
            server_name = row['name']
            
            grades = fetch_toolbench_grades(server_name)
            
            record = {
                'server_id': server_id,
                'source': 'arcade_toolbench',
                'security_grade': grades['security_grade'],
                'compliance_grade': grades['compliance_grade'],
                'quality_grade': grades['quality_grade'],
                'raw_html_hash': grades['raw_html_hash'],
                'error': grades['error'],
                'fetched_at': datetime.utcnow().isoformat()
            }
            
            ws_write(db, 'third_party_benchmarks', record)
            
            if grades['error']:
                errors += 1
                time.sleep(FETCH_DELAY_ERROR)
            else:
                processed += 1
                time.sleep(FETCH_DELAY_SUCCESS)
        
        db.log_run(
            status='ok',
            duration_ms=int((time.monotonic() - start) * 1000),
            processed=processed,
            errors=errors
        )
        
    except Exception as e:
        db.log_run(
            status='error',
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e)[:500]
        )
        raise

if __name__ == '__main__':
    run()