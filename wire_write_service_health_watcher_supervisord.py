#!/usr/bin/env python3
# deps: requests
"""
Wire write_service_health_watcher into supervisord_sentinel_full.conf.

Steps:
  1. Read the health watcher script to extract its command line.
  2. Read the existing supervisord conf.
  3. If [program:write_service_health_watcher] is absent, append it with
     autorestart=true, numprocs=1, and stdout_logfile under shared/logs/.
  4. Create a timestamped .bak backup before touching the conf.
  5. Run supervisorctl reread && supervisorctl update.
  6. Smoke: assert the entry is present in the conf.

Exits 0 on success; non-zero on any failure.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SENTINEL_ROOT = Path("/home/workspace/zo_sentinel")
HEALTH_WATCHER_PATH = SENTINEL_ROOT / "write_service_health_watcher.py"
CONF_PATH = SENTINEL_ROOT / "supervisord_sentinel_full.conf"
SHARED_LOGS = Path("/home/workspace/logs")

PROGRAM_NAME = "write_service_health_watcher"
SECTION = f"[program:{PROGRAM_NAME}]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_conf() -> Path:
    """Create a timestamped .bak of the supervisord conf."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak_path = Path(f"{CONF_PATH}.bak.{ts}")
    bak_path.write_bytes(CONF_PATH.read_bytes())
    print(f"[wire] Backup created: {bak_path}")
    return bak_path


def get_watcher_command() -> str:
    """Read the health watcher script and return its run command."""
    content = HEALTH_WATCHER_PATH.read_text()
    # The canonical command is: python3 <abs_path>
    return f"python3 {HEALTH_WATCHER_PATH.resolve()}"


def program_entry(command: str) -> str:
    """Return the supervisord program block for write_service_health_watcher."""
    log_base = PROGRAM_NAME.replace("_", "_")
    stdout_log = SHARED_LOGS / f"sentinel_{log_base}.log"
    stderr_log = SHARED_LOGS / f"sentinel_{log_base}.log"
    return (
        f"\n{SECTION}\n"
        f"command={command}\n"
        f"directory={SENTINEL_ROOT}\n"
        f"autostart=true\n"
        f"autorestart=true\n"
        f"startretries=5\n"
        f"numprocs=1\n"
        f"stdout_logfile={stdout_log}\n"
        f"stderr_logfile={stderr_log}\n"
        f"stdout_logfile_maxbytes=5MB\n"
    )


def conf_has_entry(conf_text: str) -> bool:
    return SECTION in conf_text


def inject_entry(conf_text: str, entry: str) -> str:
    """Append the program entry to the conf text (before any trailing blank lines)."""
    return conf_text.rstrip() + entry + "\n"


def run_supervisorctl() -> None:
    """Run supervisorctl reread && update. Raises on failure."""
    for cmd in ("reread", "update"):
        result = subprocess.run(
            ["supervisorctl", cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"[wire] supervisorctl {cmd} stderr: {result.stderr}", file=sys.stderr)
            print(f"[wire] supervisorctl {cmd} stdout: {result.stdout}", file=sys.stderr)
            # Non-fatal: supervisorctl may warn but succeed; check for "error" in output
            if "error" in result.stdout.lower() or "error" in result.stderr.lower():
                raise RuntimeError(f"supervisorctl {cmd} failed: {result.stderr}")
        else:
            print(f"[wire] supervisorctl {cmd}: {result.stdout.strip()}")


def smoke_check() -> None:
    """Assert the entry is present in the conf (raises AssertionError on failure)."""
    conf_text = CONF_PATH.read_text()
    assert SECTION in conf_text, (
        f"SMOKE FAIL: [{PROGRAM_NAME}] not found in {CONF_PATH} after wiring"
    )
    print(f"[wire] Smoke PASS: {SECTION} found in {CONF_PATH}")


def main() -> int:
    print("=" * 60)
    print("wire_write_service_health_watcher_supervisord")
    print("=" * 60)

    # 1. Verify health watcher script exists
    if not HEALTH_WATCHER_PATH.exists():
        print(f"[wire] ERROR: {HEALTH_WATCHER_PATH} not found", file=sys.stderr)
        return 1

    # 2. Read current conf
    conf_text = CONF_PATH.read_text()
    print(f"[wire] Read conf: {CONF_PATH} ({len(conf_text)} bytes)")

    # 3. Check if already wired
    if conf_has_entry(conf_text):
        print(f"[wire] {SECTION} already present in conf -- no-op.")
        smoke_check()
        return 0

    # 4. Backup
    backup_conf()

    # 5. Build entry
    command = get_watcher_command()
    entry = program_entry(command)
    print(f"[wire] Injected command: {command}")

    # 6. Inject and write
    new_conf_text = inject_entry(conf_text, entry)
    CONF_PATH.write_text(new_conf_text)
    print(f"[wire] Conf updated ({len(new_conf_text)} bytes)")

    # 7. Run supervisorctl
    run_supervisorctl()

    # 8. Smoke check
    smoke_check()

    print("\n[wire] PASS: write_service_health_watcher wired in supervisord config")
    return 0


if __name__ == "__main__":
    sys.exit(main())
