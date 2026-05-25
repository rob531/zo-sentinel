import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


FAILED_MODULES = [
    'build_start_all_sh',
    'build_graphql_schema',
    'build_email_guid_auth',
    'build_mcp_detail_view_ui',
    'build_advanced_filter_api',
    'build_forensic_detail_api',
    'build_manual_override_api',
    'build_compliance_export_service',
    'build_supervisor_auto_update',
]

BUILD_QUEUE_TABLE = 'build_queue'
BUILD_HISTORY_TABLE = 'build_history'


def get_failed_modules() -> list[str]:
    return FAILED_MODULES.copy()


def get_import_error_patterns() -> list[str]:
    return [
        'ModuleNotFoundError',
        'ImportError',
        'cannot import name',
        'No module named',
        'File "<frozen importlib"',
    ]


def retry_failed_build(
    module_name: str,
    write_service_url: str = 'http://127.0.0.1:8772/write',
    max_retries: int = 3
) -> dict:
    retry_count = 0
    last_error = None
    
    while retry_count < max_retries:
        try:
            payload = {
                'table': BUILD_QUEUE_TABLE,
                'rows': {
                    'module_name': module_name,
                    'queued_at': datetime.utcnow().isoformat(),
                    'retry_attempt': retry_count + 1,
                    'priority': 'high',
                    'triggered_by': 'retry_failed_build_modules'
                },
                'wait': True
            }
            
            import requests
            response = requests.post(write_service_url, json=payload, timeout=30)
            response.raise_for_status()
            
            logger.info(f"Successfully re-queued module: {module_name} (attempt {retry_count + 1})")
            return {
                'module_name': module_name,
                'status': 'queued',
                'retry_attempt': retry_count + 1,
                'response': response.json() if response.content else None
            }
            
        except Exception as e:
            retry_count += 1
            last_error = str(e)
            logger.warning(f"Retry {retry_count} failed for {module_name}: {e}")
            if retry_count < max_retries:
                time.sleep(2 ** retry_count)
    
    logger.error(f"All retries exhausted for {module_name}: {last_error}")
    return {
        'module_name': module_name,
        'status': 'failed',
        'error': last_error,
        'total_attempts': max_retries
    }


def record_build_history(module_name: str, status: str, error: Optional[str] = None) -> None:
    try:
        payload = {
            'table': BUILD_HISTORY_TABLE,
            'rows': {
                'module_name': module_name,
                'status': status,
                'attempted_at': datetime.utcnow().isoformat(),
                'error_detail': error
            },
            'wait': True
        }
        
        import requests
        response = requests.post('http://127.0.0.1:8772/write', json=payload, timeout=30)
        response.raise_for_status()
        logger.info(f"Recorded build history for {module_name}: {status}")
        
    except Exception as e:
        logger.error(f"Failed to record build history for {module_name}: {e}")


def run() -> dict:
    logger.info("Starting retry of failed build modules")
    logger.info(f"Modules to retry: {len(FAILED_MODULES)}")
    
    results = []
    
    for module_name in FAILED_MODULES:
        logger.info(f"Processing module: {module_name}")
        result = retry_failed_build(module_name)
        results.append(result)
        
        if result['status'] == 'queued':
            record_build_history(module_name, 're_queued')
        else:
            record_build_history(module_name, 'retry_failed', result.get('error'))
        
        time.sleep(0.5)
    
    successful = sum(1 for r in results if r['status'] == 'queued')
    failed = sum(1 for r in results if r['status'] == 'failed')
    
    summary = {
        'total_modules': len(FAILED_MODULES),
        'successful_queues': successful,
        'failed_queues': failed,
        'results': results,
        'completed_at': datetime.utcnow().isoformat()
    }
    
    logger.info(f"Retry complete: {successful} queued, {failed} failed")
    return summary


if __name__ == '__main__':
    run()