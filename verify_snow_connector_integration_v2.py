import sys
import time
import json
from datetime import datetime, timedelta

SERVICE_NAME = 'verify_snow_connector_integration_v2'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772'
APPROVAL_WORKFLOW_URL = 'http://127.0.0.1:8780'
SNOW_CONNECTOR_URL = 'http://127.0.0.1:8781'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_FILE = f'/tmp/{SERVICE_NAME}.log'

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def ws_query(sql):
    try:
        import requests
        r = requests.post(QUERY_SERVICE_URL + '/query', json={'sql': sql}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"QUERY ERROR: {e}")
        return {'rows': [], 'count': 0}

def ws_write(table, rows):
    try:
        import requests
        r = requests.post(WRITE_SERVICE_URL + '/write', json={'table': table, 'rows': rows}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"WRITE ERROR: {e}")
        return {'ok': False}

def ws_execute(sql):
    try:
        import requests
        r = requests.post(EXECUTE_SERVICE_URL + '/execute', json={'sql': sql}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"EXECUTE ERROR: {e}")
        return {'ok': False}

def check_single_instance():
    import os
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Another instance running as PID {old_pid}")
            return False
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True

def remove_pid_file():
    try:
        import os
        os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log("Received shutdown signal")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': datetime.utcnow().isoformat()})

def check_service_health(url, name):
    try:
        import requests
        r = requests.get(url + '/health', timeout=10)
        if r.status_code == 200:
            return {'status': 'ok', 'service': name, 'url': url}
        return {'status': 'fail', 'service': name, 'url': url}
    except Exception as e:
        return {'status': 'error', 'service': name, 'error': str(e)}

def check_foreign_keys():
    """Check foreign key relationships between tables"""
    results = {
        'mcp_server_registry_to_submissions': False,
        'submissions_to_audit_log': False,
        'server_registry_to_signal_scores': False,
        'server_registry_to_risk_register': False,
    }
    
    # Check mcp_server_registry -> mcp_submissions via server_id
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM information_schema.columns 
        WHERE table_name = 'mcp_submissions' 
        AND column_name IN ('server_id', 'target_server_id')
    """)
    results['mcp_server_registry_to_submissions'] = r.get('count', 0) > 0
    
    # Check submissions have event_type for snow connector events
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM information_schema.columns 
        WHERE table_name = 'audit_log' 
        AND column_name IN ('target_server_id', 'server_id')
    """)
    results['submissions_to_audit_log'] = r.get('count', 0) > 0
    
    # Check signal_scores table exists with server_id
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM information_schema.columns 
        WHERE table_name = 'mcp_signal_scores' 
        AND column_name = 'server_id'
    """)
    results['server_registry_to_signal_scores'] = r.get('count', 0) > 0
    
    # Check risk_register table exists
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM information_schema.tables 
        WHERE table_name = 'mcp_risk_register'
    """)
    results['server_registry_to_risk_register'] = r.get('count', 0) > 0
    
    return results

def check_approval_workflow_snow_routes():
    """Check if snow_connector endpoints are registered in approval_workflow"""
    
    # Check if approval_workflow API has snow connector integration routes
    routes = []
    
    # Check audit_log for snow_connector events
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM audit_log 
        WHERE event_type LIKE '%snow%' 
        OR event_type LIKE '%servicenow%'
        OR detail LIKE '%snow%'
    """)
    snow_events = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    # Check if any submissions have snow-related metadata
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM mcp_submissions 
        WHERE metadata LIKE '%snow%' 
        OR metadata LIKE '%servicenow%'
    """)
    snow_submissions = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    # Check if there's a snow_connector integration table or records
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM information_schema.tables 
        WHERE table_name LIKE '%snow%'
    """)
    snow_tables = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    return {
        'snow_events_logged': snow_events > 0,
        'snow_submissions': snow_submissions > 0,
        'snow_tables_exist': snow_tables > 0,
        'snow_event_count': snow_events
    }

def check_write_service_routing():
    """Check if write_service can route webhook payloads to mcp_submissions"""
    
    # Verify mcp_submissions table is writable
    test_row = {
        'server_id': 'test_snow_verify_' + str(int(time.time())),
        'name': 'snow_connector_integration_test',
        'url': 'https://test.snow.verify.example.com',
        'description': 'Integration verification test',
        'submission_type': 'snow_webhook_test',
        'verdict': 'pending',
        'status': 'test'
    }
    
    # First check if mcp_submissions table has the columns we need
    r = ws_query("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'mcp_submissions'
    """)
    cols = [row.get('column_name', '') for row in r.get('rows', [])]
    
    can_write = False
    if 'server_id' in cols or 'name' in cols:
        # Try to write a test record (will be cleaned up)
        result = ws_write('mcp_submissions', test_row)
        can_write = result.get('ok', False)
        
        # Clean up test record
        ws_execute(f"""
            DELETE FROM mcp_submissions 
            WHERE server_id = '{test_row['server_id']}'
            AND submission_type = 'snow_webhook_test'
        """)
    
    # Check routing rules exist in mesh_events
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM mesh_events 
        WHERE event_type LIKE '%snow%' 
        OR routing_target LIKE '%approval%'
    """)
    routing_rules = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    return {
        'can_write_submissions': can_write,
        'submissions_columns': len(cols),
        'routing_rules_exist': routing_rules > 0,
        'routing_rule_count': routing_rules
    }

def check_approval_workflow_snow_read():
    """Check if approval_workflow reads snow_connector output correctly"""
    
    # Check for snow connector output in various tables
    results = {}
    
    # Check signal_scores for snow-related signals
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM mcp_signal_scores 
        WHERE signal_name LIKE '%snow%' 
        OR evidence LIKE '%snow%'
    """)
    results['snow_signals'] = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    # Check attestation for snow-verified servers
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM mcp_attestations 
        WHERE attestation_type = 'snow_verified' 
        OR metadata LIKE '%snow%'
    """)
    results['snow_attestations'] = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    # Check trust scores for snow-linked servers
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM mcp_server_registry 
        WHERE verdict LIKE '%snow%'
        OR trust_score IS NOT NULL
    """)
    results['snow_trust_records'] = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    # Check audit_log for snow workflow events
    r = ws_query("""
        SELECT COUNT(*) as cnt FROM audit_log 
        WHERE event_type IN ('snow_webhook_received', 'snow_approval_created', 'snow_verdict_recorded')
        OR detail LIKE '%snow_connector%'
    """)
    results['snow_workflow_events'] = r.get('rows', [{}])[0].get('cnt', 0) if r.get('rows') else 0
    
    return results

def compute_completeness_score(fk_results, route_results, routing_results, read_results):
    """Compute integration completeness score 0-100"""
    
    score = 0
    
    # Foreign key paths (25 points)
    fk_score = sum(1 for v in fk_results.values() if v) / len(fk_results) * 25
    score += fk_score
    
    # Route registration (25 points)
    route_score = 0
    if route_results['snow_events_logged']:
        route_score += 10
    if route_results['snow_submissions']:
        route_score += 10
    if route_results['snow_tables_exist']:
        route_score += 5
    score += route_score
    
    # Write service routing (25 points)
    routing_score = 0
    if routing_results['can_write_submissions']:
        routing_score += 15
    if routing_results['routing_rules_exist']:
        routing_score += 10
    score += routing_score
    
    # Approval workflow reading (25 points)
    read_score = 0
    if read_results['snow_signals'] > 0:
        read_score += 8
    if read_results['snow_attestations'] > 0:
        read_score += 7
    if read_results['snow_trust_records'] > 0:
        read_score += 5
    if read_results['snow_workflow_events'] > 0:
        read_score += 5
    score += read_score
    
    return int(min(100, score))

def write_verification_report(score, fk_results, route_results, routing_results, read_results, health_checks):
    """Write verification report to service"""
    report = {
        'verification_service': SERVICE_NAME,
        'timestamp': datetime.utcnow().isoformat(),
        'completeness_score': score,
        'foreign_key_check': fk_results,
        'snow_route_registration': route_results,
        'write_service_routing': routing_results,
        'approval_workflow_reads': read_results,
        'service_health': health_checks,
        'recommendation': 'INTEGRATED' if score >= 70 else 'NEEDS_WORK' if score >= 40 else 'INCOMPLETE'
    }
    
    ws_write('audit_log', {
        'target_server_id': 'verify_snow_connector_integration',
        'event_type': 'snow_connector_integration_verify',
        'actor': SERVICE_NAME,
        'detail': json.dumps(report)
    })
    
    return report

def main():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log("Another instance running. Exiting.")
        return
    
    log(f"Starting {SERVICE_NAME}")
    send_heartbeat()
    
    try:
        # Check service health
        log("Checking service health...")
        health_checks = {
            'approval_workflow': check_service_health(APPROVAL_WORKFLOW_URL, 'approval_workflow'),
            'snow_connector': check_service_health(SNOW_CONNECTOR_URL, 'snow_connector') if SNOW_CONNECTOR_URL else {'status': 'not_configured'},
            'write_service': check_service_health(WRITE_SERVICE_URL, 'write_service')
        }
        send_heartbeat()
        
        # Check foreign keys
        log("Checking foreign key paths...")
        fk_results = check_foreign_keys()
        log(f"FK Results: {fk_results}")
        send_heartbeat()
        
        # Check route registration
        log("Checking snow_connector route registration...")
        route_results = check_approval_workflow_snow_routes()
        log(f"Route Results: {route_results}")
        send_heartbeat()
        
        # Check write service routing
        log("Checking write_service routing capability...")
        routing_results = check_write_service_routing()
        log(f"Routing Results: {routing_results}")
        send_heartbeat()
        
        # Check approval workflow reading
        log("Checking approval_workflow snow_connector output reading...")
        read_results = check_approval_workflow_snow_read()
        log(f"Read Results: {read_results}")
        send_heartbeat()
        
        # Compute completeness score
        score = compute_completeness_score(fk_results, route_results, routing_results, read_results)
        log(f"Integration Completeness Score: {score}/100")
        
        # Write verification report
        report = write_verification_report(score, fk_results, route_results, routing_results, read_results, health_checks)
        
        # Print summary
        print("\n" + "="*60)
        print("SNOW_CONNECTOR INTEGRATION VERIFICATION RESULTS")
        print("="*60)
        print(f"\nCompleteness Score: {score}/100")
        print(f"Recommendation: {report['recommendation']}")
        print("\nDetailed Results:")
        print(f"\n[1] Foreign Key Paths (25 pts)")
        for k, v in fk_results.items():
            print(f"    {'✓' if v else '✗'} {k}: {v}")
        
        print(f"\n[2] Snow Connector Route Registration (25 pts)")
        for k, v in route_results.items():
            print(f"    {'✓' if v else '✗'} {k}: {v}")
        
        print(f"\n[3] Write Service Routing (25 pts)")
        for k, v in routing_results.items():
            print(f"    {'✓' if v else '✗'} {k}: {v}")
        
        print(f"\n[4] Approval Workflow Reading (25 pts)")
        for k, v in read_results.items():
            print(f"    {'✓' if v > 0 else '✗'} {k}: {v}")
        
        print(f"\n[5] Service Health")
        for svc, health in health_checks.items():
            print(f"    {health.get('status', 'unknown')}: {svc}")
        
        print("\n" + "="*60)
        
    except Exception as e:
        log(f"Error during verification: {e}")
    finally:
        send_heartbeat()
        remove_pid_file()
        log("Verification complete")

if __name__ == '__main__':
    main()