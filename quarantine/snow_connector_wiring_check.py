#!/usr/bin/env python3
"""
snow_connector_wiring_check.py
Diagnostic: Verify snow_connector.py integration into approval_workflow.
Output: wiring status, missing integration points, file+line references.
Handler: diagnostic_report. Priority: 0.85.
"""
import sys
import os
import datetime
from pathlib import Path

SERVICE_NAME = "snow_connector_wiring_check"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f"{SERVICE_NAME}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
import requests

def ws_write(table, rows):
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("ws_write failed: %s", e)
        return {"ok": False, "error": str(e)}

def ws_query(sql):
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("ws_query failed: %s", e)
        return {"rows": [], "error": str(e)}

def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return ""

def check_file_exists(path_str):
    p = Path(path_str)
    return p.exists(), p

def diagnose_wiring():
    report = {
        "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
        "service": SERVICE_NAME,
        "status": "in_progress",
        "checks": {}
    }
    
    # 1. Read snow_connector.py
    snow_connector_path = "/home/workspace/zo_sentinel/snow_connector.py"
    snow_connector_exists, snow_connector_abs = check_file_exists(snow_connector_path)
    report["checks"]["snow_connector_exists"] = snow_connector_exists
    
    snow_connector_content = ""
    if snow_connector_exists:
        snow_connector_content = read_file(snow_connector_path)
        logger.info("snow_connector.py found, size: %d bytes", len(snow_connector_content))
        
        # Check LOG_DIR definition
        log_dir_lines = [l for l in snow_connector_content.split('\n') if 'LOG_DIR' in l and '=' in l]
        report["checks"]["log_dir_definition"] = log_dir_lines
        
        # Check for Path import
        has_path_import = 'from pathlib import Path' in snow_connector_content or 'import pathlib' in snow_connector_content
        report["checks"]["has_path_import"] = has_path_import
        
        # Check if LOG_DIR is string vs Path
        for line in log_dir_lines:
            if 'LOG_DIR' in line and '.mkdir' in snow_connector_content[snow_connector_content.index(line):snow_connector_content.index(line)+200]:
                logger.info("Found LOG_DIR usage with .mkdir(): %s", line.strip())
        
        # Check for approval_workflow import
        imports_aw = 'approval_workflow' in snow_connector_content
        report["checks"]["imports_approval_workflow"] = imports_aw
        
        # Check for ServiceNow webhook endpoint references
        has_snow_webhook = 'webhook' in snow_connector_content.lower() or 'snow' in snow_connector_content.lower()
        report["checks"]["has_snow_webhook_reference"] = has_snow_webhook
        
    # 2. Read approval_workflow.py
    aw_path = "/home/workspace/zo_sentinel/approval_workflow.py"
    aw_exists, aw_abs = check_file_exists(aw_path)
    report["checks"]["approval_workflow_exists"] = aw_exists
    
    aw_content = ""
    if aw_exists:
        aw_content = read_file(aw_path)
        logger.info("approval_workflow.py found, size: %d bytes", len(aw_content))
        
        # Check for snow_connector integration
        imports_snow = 'snow_connector' in aw_content
        report["checks"]["aw_imports_snow_connector"] = imports_snow
        
        # Check for ServiceNow webhook registration endpoint
        has_webhook_endpoint = 'webhook' in aw_content.lower() and ('@app' in aw_content or '@router' in aw_content)
        report["checks"]["has_webhook_endpoint"] = has_webhook_endpoint
        
    # 3. Query information_schema for snow_connector related tables
    snow_tables = ws_query("""
        SELECT table_name, column_name 
        FROM information_schema.columns 
        WHERE table_name ILIKE '%snow%' 
           OR table_name ILIKE '%servicenow%'
           OR column_name ILIKE '%snow%'
        ORDER BY table_name, column_name
    """)
    report["checks"]["snow_related_tables"] = snow_tables.get('rows', [])
    
    # 4. Check for pending SNOW tasks in task queue
    pending_tasks = ws_query("""
        SELECT task_id, task_name, status, priority
        FROM task_queue 
        WHERE task_name ILIKE '%snow%' 
           OR task_name ILIKE '%servicenow%'
        LIMIT 10
    """)
    report["checks"]["pending_snow_tasks"] = pending_tasks.get('rows', [])
    
    # 5. Check integration status
    integration_status = "disconnected"
    if snow_connector_exists and aw_exists:
        if imports_aw or imports_snow:
            integration_status = "partially_connected"
        if has_webhook_endpoint and has_snow_webhook:
            integration_status = "connected"
    
    report["checks"]["integration_status"] = integration_status
    
    # 6. List missing integration points
    missing_points = []
    if snow_connector_exists and not imports_aw:
        missing_points.append({
            "point": "snow_connector.py should import approval_workflow",
            "suggested_fix": "Add 'from approval_workflow import APPROVAL_WEBHOOK_URL' or similar integration",
            "file": snow_connector_path
        })
    if aw_exists and not imports_snow:
        missing_points.append({
            "point": "approval_workflow.py should import or reference snow_connector",
            "suggested_fix": "Add 'import snow_connector' or reference SNOW escalation handler",
            "file": aw_path
        })
    if not has_webhook_endpoint:
        missing_points.append({
            "point": "ServiceNow webhook registration endpoint missing",
            "suggested_fix": "Add @app.post('/api/servicenow/webhook') endpoint to approval_workflow.py",
            "file": aw_path
        })
    
    report["checks"]["missing_integration_points"] = missing_points
    
    # 7. Specific file+line references where connection should be added
    connection_references = []
    if snow_connector_exists:
        # Find where LOG_DIR is defined
        lines = snow_connector_content.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('LOG_DIR') and '=' in line:
                if 'Path' not in line:
                    connection_references.append({
                        "file": snow_connector_path,
                        "line": i + 1,
                        "issue": f"LOG_DIR is string, not Path object: {line.strip()}",
                        "fix": "Change to: LOG_DIR = Path('/home/workspace/logs')"
                    })
                else:
                    connection_references.append({
                        "file": snow_connector_path,
                        "line": i + 1,
                        "issue": f"LOG_DIR defined correctly as Path: {line.strip()}",
                        "fix": "No change needed"
                    })
    
    report["checks"]["connection_references"] = connection_references
    
    # Determine final status
    if integration_status == "connected":
        report["status"] = "pass"
        report["summary"] = "snow_connector.py is properly wired into approval_workflow"
    elif integration_status == "partially_connected":
        report["status"] = "partial"
        report["summary"] = "snow_connector.py has some integration but missing key connection points"
    else:
        report["status"] = "fail"
        report["summary"] = "snow_connector.py is NOT wired into approval_workflow"
    
    return report

def main():
    logger.info("Starting snow_connector wiring diagnostic")
    
    report = diagnose_wiring()
    
    logger.info("Wiring status: %s", report['checks'].get('integration_status', 'unknown'))
    logger.info("Missing integration points: %d", len(report['checks'].get('missing_integration_points', [])))
    
    # Write diagnostic report to service_health
    heartbeat = {
        "service": SERVICE_NAME,
        "status": report['status'],
        "last_heartbeat": datetime.datetime.now(timezone.utc).isoformat(),
        "meta": {
            "integration_status": report['checks'].get('integration_status', 'unknown'),
            "snow_tables_found": len(report['checks'].get('snow_related_tables', [])),
            "missing_points_count": len(report['checks'].get('missing_integration_points', []))
        }
    }
    ws_write("service_health", [heartbeat])
    
    # Print report
    import json
    print(json.dumps(report, indent=2, default=str))
    
    logger.info("Diagnostic complete. Status: %s", report['status'])
    
    # Exit with appropriate code
    if report['status'] == 'pass':
        sys.exit(0)
    elif report['status'] == 'partial':
        sys.exit(1)
    else:
        sys.exit(2)

if __name__ == '__main__':
    main()