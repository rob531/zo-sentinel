#!/usr/bin/env python3
"""
Diagnostic utility to investigate self_diagnostics daemon staleness.
Queries service_health table, checks process status, and inspects heartbeat logic.
"""

import os
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

# Add project paths
sys.path.insert(0, '/opt/sentinel')
sys.path.insert(0, '/opt/sentinel/lib')

try:
    import psutil
except ImportError:
    psutil = None


def get_current_timestamp():
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc)


def query_heartbeat_via_write_service(endpoint="http://localhost:8772"):
    """
    Query service_health table for self_diagnostics heartbeat.
    Uses write_service HTTP endpoint to read current state.
    """
    import urllib.request
    import urllib.error
    
    try:
        # Attempt to query via write_service's status/read endpoint
        url = f"{endpoint}/status/service_health/self_diagnostics"
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            
        # Parse heartbeat timestamp from response
        if 'last_heartbeat' in data:
            return {
                'last_heartbeat_ts': data['last_heartbeat'],
                'heartbeat_age_seconds': (get_current_timestamp() - 
                    datetime.fromisoformat(data['last_heartbeat'].replace('Z', '+00:00'))).total_seconds(),
                'source': 'write_service_http'
            }
        elif 'heartbeat_ts' in data:
            return {
                'last_heartbeat_ts': data['heartbeat_ts'],
                'heartbeat_age_seconds': (get_current_timestamp() - 
                    datetime.fromisoformat(data['heartbeat_ts'].replace('Z', '+00:00'))).total_seconds(),
                'source': 'write_service_http'
            }
        elif 'data' in data and isinstance(data['data'], dict):
            for key in ['last_heartbeat', 'heartbeat_ts', 'timestamp']:
                if key in data['data']:
                    ts = data['data'][key]
                    return {
                        'last_heartbeat_ts': ts,
                        'heartbeat_age_seconds': (get_current_timestamp() - 
                            datetime.fromisoformat(ts.replace('Z', '+00:00'))).total_seconds(),
                        'source': 'write_service_http'
                    }
                    
    except Exception as e:
        pass
    
    # Fallback: try direct database query via db_utils pattern
    try:
        from db_utils import get_health_status
        result = get_health_status('self_diagnostics')
        if result and 'last_heartbeat' in result:
            ts = result['last_heartbeat']
            return {
                'last_heartbeat_ts': ts,
                'heartbeat_age_seconds': (get_current_timestamp() - 
                    datetime.fromisoformat(ts.replace('Z', '+00:00'))).total_seconds(),
                'source': 'db_utils'
            }
    except ImportError:
        pass
    except Exception:
        pass
    
    return None


def check_process_alive():
    """
    Check if self_diagnostics daemon process is still running.
    Uses psutil or /proc inspection.
    """
    process_name = 'self_diagnostics'
    
    # Method 1: psutil
    if psutil:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
            try:
                proc_info = proc.info
                name = proc_info.get('name', '')
                cmdline = ' '.join(proc_info.get('cmdline', []))
                
                if process_name in name or process_name in cmdline:
                    return {
                        'alive': True,
                        'pid': proc_info['pid'],
                        'name': proc_info['name'],
                        'create_time': datetime.fromtimestamp(proc_info['create_time'], tz=timezone.utc),
                        'method': 'psutil'
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    
    # Method 2: /proc inspection (Linux)
    if os.path.exists('/proc'):
        for pid_str in os.listdir('/proc'):
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            try:
                cmdline_path = f'/proc/{pid}/cmdline'
                with open(cmdline_path, 'r') as f:
                    cmdline = f.read().replace('\x00', ' ').strip()
                
                if process_name in cmdline:
                    comm_path = f'/proc/{pid}/comm'
                    name = ''
                    try:
                        with open(comm_path, 'r') as f:
                            name = f.read().strip()
                    except:
                        pass
                    
                    stat_path = f'/proc/{pid}/stat'
                    create_time = None
                    try:
                        with open(stat_path, 'r') as f:
                            stat = f.read().split()
                            # st_atime is at index 18 (0-indexed: 17)
                            if len(stat) > 20:
                                # Get starttime and calculate boot time
                                starttime = float(stat[20])
                                with open('/proc/uptime', 'r') as uf:
                                    uptime = float(uf.read().split()[0])
                                btime_cmd = '/proc/stat'
                                with open(btime_cmd, 'r') as bf:
                                    for line in bf:
                                        if line.startswith('btime'):
                                            btime = float(line.split()[1])
                                            break
                                create_time = datetime.fromtimestamp(
                                    btime + uptime - (starttime / 100.0), 
                                    tz=timezone.utc
                                )
                    except:
                        pass
                    
                    return {
                        'alive': True,
                        'pid': pid,
                        'name': name or process_name,
                        'create_time': create_time,
                        'method': 'proc'
                    }
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
    
    return {
        'alive': False,
        'pid': None,
        'name': None,
        'create_time': None,
        'method': 'none'
    }


def inspect_heartbeat_logic():
    """
    Inspect self_diagnostics.py for heartbeat write logic correctness.
    Checks table name, row format, and potential issues.
    """
    possible_paths = [
        '/opt/sentinel/daemons/self_diagnostics.py',
        '/opt/sentinel/self_diagnostics.py',
        '/opt/sentinel/bin/self_diagnostics.py',
        './self_diagnostics.py',
        '../daemons/self_diagnostics.py',
        '/opt/sentinel/services/self_diagnostics.py',
    ]
    
    findings = {
        'file_found': False,
        'table_name': None,
        'heartbeat_field': None,
        'row_format_correct': None,
        'potential_issues': [],
        'logic_analysis': None
    }
    
    source_code = None
    source_path = None
    
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    source_code = f.read()
                source_path = path
                findings['file_found'] = True
                break
            except:
                continue
    
    if not source_code:
        # Search in common locations
        for root in ['/opt/sentinel', '/opt', '.']:
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if filename == 'self_diagnostics.py':
                        path = os.path.join(dirpath, filename)
                        try:
                            with open(path, 'r') as f:
                                source_code = f.read()
                            source_path = path
                            findings['file_found'] = True
                            break
                        except:
                            continue
                if source_code:
                    break
            if source_code:
                break
    
    if not source_code:
        findings['potential_issues'].append('Could not locate self_diagnostics.py source file')
        findings['logic_analysis'] = 'unknown'
        return findings
    
    # Analyze source code
    lines = source_code.split('\n')
    
    # Check for service_health table usage
    table_references = []
    for i, line in enumerate(lines, 1):
        if 'service_health' in line.lower():
            table_references.append((i, line.strip()))
            if "table" in line.lower() or "'service_health'" in line.lower() or '"service_health"' in line.lower():
                findings['table_name'] = 'service_health'
    
    # Check for heartbeat field
    heartbeat_patterns = [
        ('last_heartbeat', 'last_heartbeat'),
        ('heartbeat_ts', 'heartbeat_ts'),
        ('timestamp', 'timestamp'),
        ('heartbeat', 'heartbeat'),
    ]
    
    for i, line in enumerate(lines, 1):
        for pattern, field_name in heartbeat_patterns:
            if pattern in line.lower() and '=' in line:
                findings['heartbeat_field'] = field_name
                break
    
    # Check for write_service endpoint usage
    write_service_found = False
    endpoint_found = None
    for i, line in enumerate(lines, 1):
        if '8772' in line:
            endpoint_found = '8772'
            write_service_found = True
        if 'write_service' in line.lower():
            write_service_found = True
    
    findings['logic_analysis'] = 'write_service' if write_service_found else 'unknown'
    
    # Check for common logic bugs
    issues = []
    
    # Issue 1: Wrong table name
    if "'service_health'" not in source_code and '"service_health"' not in source_code:
        if 'service_health' not in source_code:
            issues.append('service_health table reference not found in source')
    
    # Issue 2: Missing heartbeat timestamp format
    datetime_imports = any('datetime' in line or 'from datetime' in line for line in lines)
    if not datetime_imports and 'isoformat' not in source_code:
        issues.append('No datetime formatting found for heartbeat timestamp')
    
    # Issue 3: Check for write failure handling
    if 'except' not in source_code or source_code.count('except') < 2:
        issues.append('Insufficient exception handling around write operations')
    
    # Issue 4: Check for timeout issues
    timeout_patterns = ['timeout', 'Timeout', 'TIMEOUT']
    has_timeout = any(p in source_code for p in timeout_patterns)
    if not has_timeout:
        issues.append('No timeout configuration found for write_service calls')
    
    # Issue 5: Check write logic flow
    in_write_block = False
    write_block_has_try = False
    for line in lines:
        if 'def write' in line or 'def update' in line or 'def send' in line:
            in_write_block = True
        if in_write_block and 'try' in line:
            write_block_has_try = True
        if in_write_block and 'return' in line and 'success' not in line.lower():
            if not write_block_has_try:
                issues.append('Write function may not handle failures properly')
            in_write_block = False
    
    findings['potential_issues'] = issues
    
    # Determine row format correctness
    if findings['table_name'] and findings['heartbeat_field'] and write_service_found:
        findings['row_format_correct'] = True
    elif findings['table_name'] or write_service_found:
        findings['row_format_correct'] = 'partial'
    else:
        findings['row_format_correct'] = False
    
    return findings


def diagnose_self_diagnostics_heartbeat_stale():
    """
    Main diagnostic function for self_diagnostics heartbeat staleness.
    Returns structured findings dict.
    """
    findings = {
        'process_alive': None,
        'heartbeat_age_seconds': None,
        'last_heartbeat_ts': None,
        'suspected_cause': None,
        'recommended_action': None,
        'diagnostics': {}
    }
    
    # 1. Check process status
    process_info = check_process_alive()
    findings['process_alive'] = process_info['alive']
    findings['diagnostics']['process_info'] = process_info
    
    # 2. Query heartbeat timestamp
    heartbeat_info = query_heartbeat_via_write_service()
    if heartbeat_info:
        findings['heartbeat_age_seconds'] = heartbeat_info['heartbeat_age_seconds']
        findings['last_heartbeat_ts'] = heartbeat_info['last_heartbeat_ts']
        findings['diagnostics']['heartbeat_source'] = heartbeat_info['source']
    else:
        findings['heartbeat_age_seconds'] = None
        findings['last_heartbeat_ts'] = None
        findings['diagnostics']['heartbeat_source'] = 'unavailable'
    
    # 3. Inspect heartbeat logic
    logic_analysis = inspect_heartbeat_logic()
    findings['diagnostics']['logic_analysis'] = logic_analysis
    
    # 4. Determine suspected cause
    age = findings['heartbeat_age_seconds']
    process_dead = not findings['process_alive']
    logic_issues = logic_analysis.get('potential_issues', [])
    logic_correct = logic_analysis.get('row_format_correct', False)
    write_service_found = logic_analysis.get('logic_analysis') == 'write_service'
    
    if process_dead:
        findings['suspected_cause'] = 'process_death'
        findings['recommended_action'] = (
            'Restart the self_diagnostics daemon. Process was found dead. '
            'Check systemd/journalctl for crash logs or OOM kills.'
        )
    elif age and age > 600:  # Stale for more than 10 minutes
        if not write_service_found:
            findings['suspected_cause'] = 'logic_bug'
            findings['recommended_action'] = (
                'Heartbeat logic may not be using write_service endpoint. '
                'Verify self_diagnostics.py sends heartbeat to :8772 write_service.'
            )
        elif logic_issues:
            findings['suspected_cause'] = 'logic_bug'
            findings['recommended_action'] = (
                f'Potential logic issues detected: {"; ".join(logic_issues)}. '
                'Review heartbeat write code for correct table/field usage and error handling.'
            )
        elif not logic_correct:
            findings['suspected_cause'] = 'logic_bug'
            findings['recommended_action'] = (
                'Heartbeat row format may be incorrect. Verify service_health table '
                'updates with correct last_heartbeat field format.'
            )
        else:
            findings['suspected_cause'] = 'write_service_timeout'
            findings['recommended_action'] = (
                'write_service endpoint may be unresponsive or timing out. '
                'Check write_service health at :8772 and database connectivity.'
            )
    elif age and age > 120:  # Stale for more than 2 minutes
        findings['suspected_cause'] = 'possible_timeout'
        findings['recommended_action'] = (
            'Heartbeat slightly stale. Monitor for resolution or check '
            'write_service responsiveness at :8772.'
        )
    else:
        findings['suspected_cause'] = 'unknown'
        findings['recommended_action'] = (
            'Cannot definitively determine cause. Heartbeat may have been '
            'recovered. Verify current daemon health.'
        )
    
    return findings


def main():
    """Main entry point."""
    print("=" * 60)
    print("SELF_DIAGNOSTICS HEARTBEAT STALENESS DIAGNOSTIC")
    print("=" * 60)
    print(f"Timestamp: {get_current_timestamp().isoformat()}")
    print()
    
    findings = diagnose_self_diagnostics_heartbeat_stale()
    
    print("FINDINGS:")
    print("-" * 40)
    print(f"  Process Alive:      {findings['process_alive']}")
    print(f"  Heartbeat Age:      {findings['heartbeat_age_seconds']:.1f}s" if findings['heartbeat_age_seconds'] else f"  Heartbeat Age:      unavailable")
    print(f"  Last Heartbeat TS:  {findings['last_heartbeat_ts']}")
    print(f"  Suspected Cause:    {findings['suspected_cause']}")
    print(f"  Recommended Action: {findings['recommended_action']}")
    print()
    
    print("DETAILED DIAGNOSTICS:")
    print("-" * 40)
    
    proc_info = findings['diagnostics'].get('process_info', {})
    print(f"  Process check method: {proc_info.get('method', 'unknown')}")
    if proc_info.get('pid'):
        print(f"  Process PID: {proc_info['pid']}")
    if proc_info.get('name'):
        print(f"  Process name: {proc_info['name']}")
    
    logic = findings['diagnostics'].get('logic_analysis', {})
    print(f"  Source file found: {logic.get('file_found', False)}")
    print(f"  Table name: {logic.get('table_name', 'unknown')}")
    print(f"  Heartbeat field: {logic.get('heartbeat_field', 'unknown')}")
    print(f"  Row format correct: {logic.get('row_format_correct', 'unknown')}")
    print(f"  Logic analysis: {logic.get('logic_analysis', 'unknown')}")
    if logic.get('potential_issues'):
        print(f"  Potential issues:")
        for issue in logic['potential_issues']:
            print(f"    - {issue}")
    
    print()
    print("STRUCTURED OUTPUT (for programmatic use):")
    print("-" * 40)
    
    # Output key findings dict
    output = {
        'process_alive': findings['process_alive'],
        'heartbeat_age_seconds': findings['heartbeat_age_seconds'],
        'last_heartbeat_ts': findings['last_heartbeat_ts'],
        'suspected_cause': findings['suspected_cause'],
        'recommended_action': findings['recommended_action']
    }
    print(json.dumps(output, indent=2))
    
    return output


if __name__ == '__main__':
    main()