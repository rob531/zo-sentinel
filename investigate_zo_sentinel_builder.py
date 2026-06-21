import os
import subprocess
import time
from datetime import datetime, timedelta

def run_command(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return "", str(e)

def investigate():
    print("--- Investigating zo_sentinel_builder Staleness ---")
    
    # 1. Check if the process is running
    print("\n1. Checking process status...")
    ps_out, ps_err = run_command("ps aux | grep zo_sentinel_builder | grep -v grep")
    if ps_out:
        print(f"Process found:\n{ps_out}")
    else:
        print("Process NOT found.")

    # 2. Check systemd service status if applicable
    print("\n2. Checking systemd service status...")
    svc_out, svc_err = run_command("systemctl status zo_sentinel_builder")
    if svc_out:
        print(svc_out)
    else:
        print("Service status unavailable or service not found.")

    # 3. Check recent logs
    print("\n3. Checking recent logs (last 20 lines)...")
    # Assuming logs might be in /var/log or accessible via journalctl
    log_out, log_err = run_command("journalctl -u zo_sentinel_builder -n 20")
    if log_out:
        print(log_out)
    else:
        # Try common log locations if journalctl fails
        log_out, log_err = run_command("tail -n 20 /var/log/zo_sentinel_builder.log")
        if log_out:
            print(log_out)
        else:
            print("No logs found.")

    # 4. Check for lock files or stale pid files
    print("\n4. Checking for stale lock/pid files...")
    lock_files = ["/tmp/zo_sentinel_builder.lock", "/var/run/zo_sentinel_builder.pid"]
    for lf in lock_files:
        if os.path.exists(lf):
            mtime = os.path.getmtime(lf)
            last_mod = datetime.fromtimestamp(mtime)
            print(f"Found lock/pid file: {lf} (Last modified: {last_mod})")
            if datetime.now() - last_mod > timedelta(hours=1):
                print(f"  WARNING: {lf} appears stale.")
        else:
            print(f"No lock/pid file at {lf}")

    # 5. Check resource usage
    print("\n5. Checking resource usage...")
    top_out, top_err = run_command("top -b -n 1 | grep zo_sentinel_builder")
    if top_out:
        print(top_out)
    else:
        print("Resource usage data unavailable.")

if __name__ == "__main__":
    investigate()