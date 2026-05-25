from datetime import datetime, timezone, timedelta
import json
import logging
import sys
import time

# Import shared utilities
sys.path.insert(0, '/home/workspace/zo_sentinel')
from threat_intel_ingestor import ws_query, ws_write, log, send_heartbeat

SERVICE_NAME = "stale_daemon_report"
SERVICE_PORT = 8772
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

STALE_THRESHOLD_HOURS = 2
REPORT_INTERVAL_SECONDS = 300

SUPERVISOR_ACTIONS = {
    "rug_pull_monitor": "supervisorctl restart rug_pull_monitor",
    "write_service": "supervisorctl restart write_service",
    "mcp_scanner": "supervisorctl restart mcp_scanner",
    "anti_entropy": "supervisorctl restart anti_entropy",
    "wisdom_synthesiser": "supervisorctl restart wisdom_synthesiser",
    "inference_router": "supervisorctl restart inference_router",
    "threat_intel_ingestor": "supervisorctl restart threat_intel_ingestor",
    "stale_daemon_report": "supervisorctl restart stale_daemon_report"
}


def calculate_age_hours(last_heartbeat_str: str) -> float:
    """Calculate hours since last heartbeat."""
    try:
        last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - last_heartbeat.replace(tzinfo=timezone.utc) if last_heartbeat.tzinfo is None else now - last_heartbeat
        return delta.total_seconds() / 3600
    except Exception:
        return float('inf')


def get_stale_daemons():
    """Query service_health and return list of stale daemons."""
    stale_daemons = []
    
    query = """
    SELECT service, last_heartbeat
    FROM service_health
    ORDER BY last_heartbeat ASC
    """
    
    try:
        result = ws_query(query)
        
        if result and 'rows' in result:
            for row in result['rows']:
                service_name = row.get('service', '')
                last_heartbeat = row.get('last_heartbeat', '')
                
                if service_name and last_heartbeat:
                    age_hours = calculate_age_hours(last_heartbeat)
                    
                    if age_hours > STALE_THRESHOLD_HOURS:
                        action = SUPERVISOR_ACTIONS.get(service_name, f"supervisorctl restart {service_name}")
                        
                        stale_daemons.append({
                            "daemon_name": service_name,
                            "last_heartbeat": last_heartbeat,
                            "age_hours": round(age_hours, 2),
                            "stale_threshold_hours": STALE_THRESHOLD_HOURS,
                            "recommended_action": action,
                            "severity": "critical" if age_hours > 24 else "warning"
                        })
                        
                        log(f"Found stale daemon: {service_name} ({age_hours:.2f}h since heartbeat)")
        
        return stale_daemons
        
    except Exception as e:
        log(f"Error querying service_health: {e}")
        return []


def generate_report(stale_daemons: list) -> dict:
    """Generate structured report from stale daemon data."""
    report = {
        "report_timestamp": datetime.now(timezone.utc).isoformat(),
        "report_type": "stale_daemon_report",
        "stale_threshold_hours": STALE_THRESHOLD_HOURS,
        "total_stale_daemons": len(stale_daemons),
        "stale_daemons": stale_daemons,
        "summary": {
            "critical_count": sum(1 for d in stale_daemons if d.get('severity') == 'critical'),
            "warning_count": sum(1 for d in stale_daemons if d.get('severity') == 'warning')
        }
    }
    
    return report


def persist_report(report: dict):
    """Write report to audit_logs table."""
    try:
        ws_write({
            'table': 'audit_logs',
            'rows': {
                f"stale_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}": {
                    'event_type': 'stale_daemon_report',
                    'target_server_id': 'zo_sentinel',
                    'event_data': json.dumps(report),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'severity': 'critical' if report['summary']['critical_count'] > 0 else 'warning'
                }
            },
            'wait': True
        })
        log("Report persisted to audit_logs")
    except Exception as e:
        log(f"Error persisting report: {e}")


def run():
    """Main daemon loop."""
    log(f"Starting {SERVICE_NAME} daemon")
    
    while True:
        try:
            send_heartbeat(SERVICE_NAME)
            
            stale_daemons = get_stale_daemons()
            
            report = generate_report(stale_daemons)
            
            log(f"Stale Daemon Report: {report['total_stale_daemons']} stale daemons found")
            log(f"  - Critical: {report['summary']['critical_count']}")
            log(f"  - Warning: {report['summary']['warning_count']}")
            
            persist_report(report)
            
            print(json.dumps(report, indent=2))
            
        except Exception as e:
            log(f"Error in daemon loop: {e}")
        
        time.sleep(REPORT_INTERVAL_SECONDS)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run()