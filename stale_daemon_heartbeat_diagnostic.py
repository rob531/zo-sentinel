import logging
import sys
import json
import requests
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'stale_daemon_heartbeat_diagnostic'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

# Expected cycle thresholds in seconds per service
CYCLE_THRESHOLDS = {
    'write_service': 300,
    'mcp_scanner': 300,
    'rug_pull_monitor': 3600,
    'anti_entropy': 300,
    'inference_router': 300,
    'threat_intel_ingestor': 600,
    'otx_ingestor': 600,
    'ecosyste_ms_ingestor': 600,
    'nvd_ingestor': 600,
    'alienvault_ingestor': 600,
    'probe_consumer': 300,
    'manager_agent': 600,
}

def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get('rows', [])

def calculate_heartbeat_age(last_heartbeat_str):
    if not last_heartbeat_str:
        return None
    try:
        last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        age_seconds = (now - last_heartbeat).total_seconds()
        return age_seconds
    except (ValueError, TypeError):
        return None

def format_age(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h{minutes}m"

def main():
    logger.info("Starting stale daemon heartbeat diagnostic")
    report = []
    report.append("=== STALE DAEMON HEARTBEAT DIAGNOSTIC REPORT ===")
    report.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report.append("")
    
    try:
        sql = """
        SELECT service_name, last_heartbeat, status, meta
        FROM service_health
        ORDER BY service_name
        """
        services = ws_query(sql)
        
        if not services:
            report.append("No services found in service_health table.")
            logger.info("No services found")
        else:
            stale_services = []
            for svc in services:
                service_name = svc.get('service_name', '')
                last_heartbeat = svc.get('last_heartbeat', '')
                threshold = CYCLE_THRESHOLDS.get(service_name, 300)
                
                if not last_heartbeat:
                    report.append(f"  - {service_name}: NO HEARTBEAT (threshold: {threshold}s)")
                    stale_services.append(service_name)
                    continue
                
                age_seconds = calculate_heartbeat_age(last_heartbeat)
                if age_seconds is None:
                    report.append(f"  - {service_name}: INVALID HEARTBEAT FORMAT ({last_heartbeat})")
                    stale_services.append(service_name)
                    continue
                
                if age_seconds > threshold:
                    age_str = format_age(age_seconds)
                    report.append(f"  - {service_name}: STALE at {age_str} (threshold: {threshold}s, last_heartbeat: {last_heartbeat})")
                    stale_services.append(service_name)
                else:
                    age_str = format_age(age_seconds)
                    report.append(f"  + {service_name}: OK at {age_str}")
            
            report.append("")
            report.append(f"Summary: {len(stale_services)} stale service(s), {len(services) - len(stale_services)} healthy")
        
    except requests.exceptions.RequestException as e:
        report.append(f"ERROR querying write_service: {e}")
        logger.error(f"Failed to query write_service: {e}")
    except Exception as e:
        report.append(f"ERROR: {e}")
        logger.error(f"Diagnostic error: {e}")
    
    report_text = '\n'.join(report)
    print(report_text)
    logger.info("Diagnostic complete")
    sys.exit(0)

if __name__ == '__main__':
    main()