#!/usr/bin/env python3
"""
Diagnostic script for gate_scheduler daemon staleness.
Checks: supervisord process state, last_exception in logs, external I/O blocking.
Diagnostic only - do NOT propose rebuild.
"""

import os
import subprocess
from datetime import datetime


def run_command(cmd, timeout=10):
    """Run shell command and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except Exception as e:
        return "", str(e), -1


def check_supervisord_state():
    """Check gate_scheduler process state via supervisord."""
    print("=" * 60)
    print("CHECK 1: Supervisord Process State")
    print("=" * 60)

    output, _, rc = run_command("supervisorctl status gate_scheduler 2>/dev/null")

    if rc != 0 or not output:
        print("[!] Unable to get supervisord status for gate_scheduler")
        return None

    print(f"Status output: {output}")

    is_running = "RUNNING" in output.upper()
    is_dead = "FATAL" in output.upper() or "STOPPED" in output.upper() or "EXITED" in output.upper()

    # Detailed process info
    print("\nDetailed process info:")
    detail_out, _, _ = run_command("ps aux | grep -E '[g]ate_scheduler' | head -5")
    if detail_out:
        for line in detail_out.split("\n"):
            if line.strip():
                print(f"  {line}")

    # Process elapsed time and CPU
    proc_info, _, _ = run_command(
        "ps -p $(supervisorctl pid gate_scheduler 2>/dev/null) "
        "-o pid,etime,cputime,stat --no-headers 2>/dev/null"
    )
    if proc_info:
        print(f"\nProcess elapsed time and CPU: {proc_info}")

    if is_dead:
        print("\n[!] PROCESS IS DEAD - Requires restart via supervisordctl")
        return "DEAD"
    elif is_running:
        print("\n[+] Process is running")
        return "RUNNING"
    else:
        print("\n[?] Process state unclear")
        return "UNKNOWN"


def check_last_exception():
    """Check logs for last_exception."""
    print("\n" + "=" * 60)
    print("CHECK 2: Last Exception in Logs")
    print("=" * 60)

    log_paths = [
        "/var/log/supervisor/gate_scheduler.log",
        "/var/log/gate_scheduler.log",
        "/var/log/gate_scheduler.err.log",
        "./logs/gate_scheduler.log",
    ]

    log_file = None
    for path in log_paths:
        if os.path.exists(path):
            log_file = path
            break

    if not log_file:
        output, _, rc = run_command("supervisorctl desc gate_scheduler 2>/dev/null")
        if rc == 0:
            print(f"Supervisord description: {output}")
        print("Log file not found in standard locations")
        return None

    print(f"Checking log file: {log_file}")

    # Look for exceptions in the last 100 lines
    output, _, rc = run_command(
        f"tail -100 '{log_file}' | grep -A 10 -i 'exception\\|error\\|traceback' | tail -30"
    )

    if output:
        print("\n--- Recent Exceptions/Errors ---")
        print(output)
        return output
    else:
        print("No exceptions found in recent logs")
        return None


def check_io_blocking():
    """Check if daemon is blocked on external I/O."""
    print("\n" + "=" * 60)
    print("CHECK 3: External I/O Blocking")
    print("=" * 60)

    pid_output, _, _ = run_command("supervisorctl pid gate_scheduler 2>/dev/null")

    if not pid_output or not pid_output.isdigit():
        print("[!] Cannot determine PID")
        return None

    pid = pid_output
    print(f"PID: {pid}")

    # Check process state (D = uninterruptible sleep, usually I/O)
    stat_out, _, _ = run_command(
        f"ps -p {pid} -o pid,stat,wchan:20 --no-headers 2>/dev/null"
    )

    if stat_out:
        print(f"Process state: {stat_out}")
        parts = stat_out.split()
        if len(parts) > 1 and "D" in parts[1]:
            print("[!] Process is in D state (uninterruptible sleep - likely blocked on I/O)")

    # Check wait channel
    wchan_out, _, _ = run_command(f"cat /proc/{pid}/wchan 2>/dev/null")
    if wchan_out:
        print(f"Current wait channel: {wchan_out}")

    # Open network connections
    print("\nOpen network connections:")
    net_out, _, _ = run_command(
        f"ss -tp 2>/dev/null | grep {pid} || "
        f"netstat -tp 2>/dev/null | grep {pid} || "
        "echo 'No connections found'"
    )
    print(net_out if net_out else "No connections found")

    # I/O stats
    print("\nRecent I/O activity:")
    io_stat, _, _ = run_command(f"cat /proc/{pid}/io 2>/dev/null | head -10")
    if io_stat:
        print(io_stat)

    # Disk I/O wait
    print("\nChecking for disk I/O wait...")
    iostat_out, _, _ = run_command(
        "iostat -x 1 1 2>/dev/null | tail -20 || echo 'iostat not available'"
    )
    if iostat_out:
        print(iostat_out)

    return True


def check_process_uptime():
    """Check if process has been running longer than threshold."""
    print("\n" + "=" * 60)
    print("CHECK 4: Process Uptime vs Stale Threshold")
    print("=" * 60)

    threshold_seconds = 180  # 3 minutes

    output, _, rc = run_command(
        "ps -p $(supervisorctl pid gate_scheduler 2>/dev/null) "
        "-o etimes= --no-headers 2>/dev/null"
    )

    if rc == 0 and output:
        try:
            uptime_seconds = int(output.strip())
            print(f"Process uptime: {uptime_seconds} seconds ({uptime_seconds / 60:.1f} minutes)")
            print(f"Stale threshold: {threshold_seconds} seconds ({threshold_seconds / 60} minutes)")

            if uptime_seconds > threshold_seconds:
                print(
                    f"[!] Process has been running {uptime_seconds - threshold_seconds} "
                    "seconds past threshold"
                )
            else:
                print("[+] Process uptime within threshold")
        except ValueError:
            print(f"Could not parse uptime: {output}")
    else:
        print("[!] Could not determine process uptime")


def main():
    print("DIAGNOSTIC: gate_scheduler daemon staleness")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("Threshold: >180s (3 minutes)")
    print()

    # Run all checks
    state = check_supervisord_state()
    exceptions = check_last_exception()
    check_io_blocking()
    check_process_uptime()

    # Summary
    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)

    if state == "DEAD":
        print("\n[CRITICAL] gate_scheduler process is DEAD")
        print("\nRECOMMENDATION: Restart via supervisordctl")
        print("  Command: supervisorctl restart gate_scheduler")
        print("\nVerify after restart with: supervisorctl status gate_scheduler")
    elif state == "RUNNING":
        if exceptions:
            print("\n[WARNING] Process is running but has recent exceptions")
            print("The staleness may be due to the last exception causing slow processing")
        else:
            print("\n[INFO] Process is running without recent exceptions")
            print("Staleness may be due to slow external I/O or normal heavy processing")
    else:
        print("\n[UNKNOWN] Unable to determine process state")


if __name__ == "__main__":
    main()
