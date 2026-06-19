#!/usr/bin/env python3
"""
Re-diagnostic for rug_pull_monitor (never seen heartbeat).
Focus: supervisord config corruption, startup failures, import errors.

Root cause suspected: supervisord-user.conf corrupted (starts with Python docstring).
"""

import os
import sys
import subprocess
import traceback
from pathlib import Path

# deps: requests

def run_cmd(cmd, timeout=30):
    """Run shell command and return (stdout, stderr, rc)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1
    except Exception as e:
        return "", str(e), 1


def check_file_header(path, expected_start):
    """Check if file starts with expected content."""
    if not os.path.exists(path):
        return None, f"File not found: {path}"
    with open(path, 'r') as f:
        first_bytes = f.read(200)
    if first_bytes.startswith(expected_start):
        return True, "OK"
    return False, f"Unexpected header: {first_bytes[:100]}"


def main():
    print("=" * 70)
    print("RUG_PULL_MONITOR DIAGNOSTIC v2 (Never Seen Heartbeat)")
    print("=" * 70)

    workspace = "/home/workspace/zo_sentinel"
    os.chdir(workspace)
    sys.path.insert(0, workspace)

    # -------------------------------------------------------------------------
    # 1. Check supervisord-user.conf CORRUPTION (primary suspect)
    # -------------------------------------------------------------------------
    print("\n[1] SUPERVISORD-USER.CONF CORRUPTION CHECK")
    print("-" * 40)

    conf_path = "/home/workspace/zo_sentinel/supervisord-user.conf"
    conf_etc = "/etc/zo/supervisord-user.conf"

    for conf in [conf_path, conf_etc]:
        if os.path.exists(conf):
            print(f"Checking: {conf}")
            with open(conf, 'r') as f:
                first_line = f.readline().strip()
            
            if first_line.startswith('"""') or first_line.startswith("'''"):
                print(f"  CORRUPTED: File starts with docstring '{first_line}'")
                print(f"  This is Python code, not INI format!")
                print(f"  First 5 lines:")
                with open(conf, 'r') as f:
                    for i, line in enumerate(f):
                        if i >= 5:
                            break
                        print(f"    {i+1}: {line.rstrip()}")
                print("  ACTION NEEDED: Fix supervisord-user.conf to be valid INI")
            elif first_line.startswith('['):
                print(f"  OK: Valid INI format, starts with [{first_line[1:]}...]")
            else:
                print(f"  UNKNOWN: First line: {first_line}")
        else:
            print(f"  Not found: {conf}")

    # -------------------------------------------------------------------------
    # 2. Check if supervisord can run at all
    # -------------------------------------------------------------------------
    print("\n[2] SUPERVISORD AVAILABILITY")
    print("-" * 40)

    stdout, stderr, rc = run_cmd("supervisorctl status 2>&1")
    if "MissingSectionHeaderError" in stderr or "No section headers" in stderr:
        print("  BROKEN: supervisord config has no section headers")
        print("  This prevents ALL supervisorctl operations")
    elif "Connection refused" in stderr or "Cannot assign requested address" in stderr:
        print("  SUPERVISORD NOT RUNNING or socket unavailable")
    elif rc == 0:
        print("  supervisorctl OK")
        for line in stdout.splitlines():
            if 'rug_pull' in line.lower():
                print(f"    {line}")
    else:
        print(f"  Error: {stderr[:200]}")

    # -------------------------------------------------------------------------
    # 3. Check rug_pull_monitor in all supervisord configs
    # -------------------------------------------------------------------------
    print("\n[3] RUG_PULL_MONITOR IN SUPERVISORD CONFIGS")
    print("-" * 40)

    config_files = [
        "/home/workspace/zo_sentinel/supervisord-user.conf",
        "/home/workspace/zo_sentinel/supervisord_sentinel.conf",
        "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf",
        "/home/workspace/zo_sentinel/supervisord_phase8_update.conf",
        "/etc/supervisor/conf.d/rug_pull_monitor.conf",
        "/etc/supervisord.d/rug_pull_monitor.conf",
    ]

    found = False
    for cf in config_files:
        if os.path.exists(cf):
            with open(cf, 'r') as f:
                content = f.read()
            if '[program:rug_pull_monitor]' in content:
                print(f"  FOUND in: {cf}")
                found = True
                # Show relevant section
                lines = content.split('\n')
                in_section = False
                for line in lines:
                    if '[program:rug_pull_monitor]' in line:
                        in_section = True
                    if in_section:
                        print(f"    {line}")
                        if line.strip().startswith('[') and 'rug_pull_monitor' not in line:
                            break

    if not found:
        print("  NOT FOUND in any config file!")
        print("  rug_pull_monitor daemon is NOT registered with supervisord")

    # -------------------------------------------------------------------------
    # 4. Check rug_pull_monitor.py source
    # -------------------------------------------------------------------------
    print("\n[4] RUG_PULL_MONITOR.PY SOURCE CHECK")
    print("-" * 40)

    py_file = "/home/workspace/zo_sentinel/rug_pull_monitor.py"
    if os.path.exists(py_file):
        print(f"  Found: {py_file}")
        with open(py_file, 'r') as f:
            content = f.read()

        # Check for critical elements
        checks = [
            ("if __name__", "__main__ entry point"),
            ("send_heartbeat", "heartbeat function"),
            ("def run(", "run() method"),
            ("HEARTBEAT_INTERVAL", "heartbeat interval constant"),
            ("service_health", "service_health table"),
        ]

        for pattern, desc in checks:
            if pattern in content:
                print(f"    OK: {desc}")
            else:
                print(f"    MISSING: {desc}")

        # Check for import errors
        print("\n  Import test:")
        import_errors = []
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('import ') or line.startswith('from '):
                mod = line.split()[1] if line.startswith('import ') else line.split()[1]
                mod = mod.split('.')[0]
                if mod in ('os', 'sys', 'json', 'time', 'hashlib', 'signal', 'requests',
                          'datetime', 'typing', 'urllib'):
                    continue  # standard library
                try:
                    __import__(mod)
                    print(f"    OK: {mod}")
                except ImportError as e:
                    print(f"    ERROR: {mod}: {e}")
                    import_errors.append(mod)
                except Exception as e:
                    print(f"    WARN: {mod}: {type(e).__name__}")
    else:
        print(f"  NOT FOUND: {py_file}")

    # -------------------------------------------------------------------------
    # 5. Check service_health table for any rug_pull entries
    # -------------------------------------------------------------------------
    print("\n[5] SERVICE_HEALTH TABLE CHECK")
    print("-" * 40)

    try:
        import requests
        resp = requests.post(
            'http://127.0.0.1:8772/query',
            json={'sql': "SELECT service, status, last_heartbeat FROM service_health WHERE service LIKE '%rug%' ORDER BY last_heartbeat DESC LIMIT 5"},
            timeout=10
        )
        data = resp.json()
        if data.get('data'):
            print("  Found rug_pull entries:")
            for row in data['data']:
                print(f"    service={row[0]}, status={row[1]}, heartbeat={row[2]}")
        else:
            print("  NO rug_pull entries in service_health table")
            print("  Confirms daemon has NEVER sent a heartbeat")
    except Exception as e:
        print(f"  Could not query service_health: {e}")

    # -------------------------------------------------------------------------
    # 6. Check for process running
    # -------------------------------------------------------------------------
    print("\n[6] PROCESS RUNNING CHECK")
    print("-" * 40)

    stdout, stderr, rc = run_cmd("ps aux | grep -i rug_pull_monitor | grep -v grep")
    if stdout.strip():
        print("  FOUND running:")
        print(stdout)
    else:
        print("  NOT RUNNING")

    # Check PID file
    pid_file = "/var/run/zo/rug_pull_monitor.pid"
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
        print(f"  PID file exists: {pid_file} -> {pid}")
    else:
        print(f"  No PID file: {pid_file}")

    # -------------------------------------------------------------------------
    # 7. Log files
    # -------------------------------------------------------------------------
    print("\n[7] LOG FILES")
    print("-" * 40)

    log_paths = [
        "/home/workspace/logs/rug_pull_monitor.log",
        "/var/log/supervisor/rug_pull_monitor.log",
        "/var/log/rug_pull_monitor.log",
    ]

    for lp in log_paths:
        if os.path.exists(lp):
            size = os.path.getsize(lp)
            print(f"  {lp}: {size} bytes")
            if size > 0:
                with open(lp, 'r') as f:
                    lines = f.readlines()
                    print(f"  Last 10 lines:")
                    for line in lines[-10:]:
                        print(f"    {line.rstrip()}")
        else:
            print(f"  Not found: {lp}")

    # -------------------------------------------------------------------------
    # DIAGNOSIS & RECOVERY
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)

    issues = []
    
    # Check for corruption
    corrupted = False
    if os.path.exists(conf_path):
        with open(conf_path, 'r') as f:
            first_line = f.readline().strip()
        if first_line.startswith('"""') or first_line.startswith("'''"):
            corrupted = True
            issues.append("CRITICAL: supervisord-user.conf is corrupted (Python docstring header)")
    
    if not found:
        issues.append("CRITICAL: rug_pull_monitor not registered in any supervisord config")
    
    if not stdout.strip():  # process not running
        issues.append("CRITICAL: rug_pull_monitor process not running")

    if issues:
        print("\nIssues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("  No obvious issues found")

    # -------------------------------------------------------------------------
    # RECOVERY STEPS
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("RECOVERY PROCEDURE")
    print("=" * 70)

    if corrupted:
        print("""
STEP 1: Fix corrupted supervisord-user.conf
------------------------------------------
The file /home/workspace/zo_sentinel/supervisord-user.conf has been corrupted.
It starts with a Python docstring instead of proper INI format.

FIX: Replace with proper INI:
""")
        print("""
[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

[supervisorctl]
serverurl=unix:///var/run/zo/supervisor.sock

[program:rug_pull_monitor]
command=python3 /home/workspace/zo_sentinel/rug_pull_monitor.py
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=3
stdout_logfile=/home/workspace/logs/rug_pull_monitor.log
stderr_logfile=/home/workspace/logs/rug_pull_monitor.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
""")

    print("""
STEP 2: After fixing config, reload supervisord:
------------------------------------------------
  supervisorctl -c /home/workspace/zo_sentinel/supervisord-user.conf reread
  supervisorctl -c /home/workspace/zo_sentinel/supervisord-user.conf update
  supervisorctl -c /home/workspace/zo_sentinel/supervisord-user.conf start rug_pull_monitor
  supervisorctl -c /home/workspace/zo_sentinel/supervisord-user.conf status rug_pull_monitor

STEP 3: Verify heartbeat appears:
------------------------------------------------
  # Wait 60 seconds for first heartbeat, then:
  python3 -c "
import requests
r = requests.post('http://127.0.0.1:8772/query', 
    json={'sql': \"SELECT * FROM service_health WHERE service='rug_pull_monitor'\"})
print(r.json())
"
""")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()