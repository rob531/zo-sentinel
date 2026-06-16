#!/usr/bin/env python3
"""
Wire enrichment_dispatcher_daemon.py into supervisord_sentinel_full.conf.

Reads existing config, checks for existing entry (idempotent), creates backup,
appends program entry, and verifies the result parses as valid ini.
"""

import configparser
import re
import shutil
import sys
from datetime import datetime, timezone

CONFIG_PATH = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"
DAEMON_SCRIPT = "/home/workspace/zo_sentinel/enrichment_dispatcher_daemon.py"
PROGRAM_NAME = "enrichment_dispatcher_daemon"
PRIORITY = 200

# The program section to append
PROGRAM_SECTION = f"""[program:{PROGRAM_NAME}]
command=python3 {DAEMON_SCRIPT}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=5
stdout_logfile=/home/workspace/logs/enrichment_dispatcher.log
stderr_logfile=/home/workspace/logs/enrichment_dispatcher.log
stdout_logfile_maxbytes=5MB
priority={PRIORITY}
"""


def has_section_raw(content: str, section_name: str) -> bool:
    """Check if section exists in raw config content."""
    pattern = re.compile(rf'^\[{re.escape(section_name)}\]\s*$', re.MULTILINE)
    return bool(pattern.search(content))


def main():
    # 1. Read existing config as raw text
    with open(CONFIG_PATH, "r") as f:
        config_content = f.read()

    # 2. Check for existing entry (idempotent)
    if has_section_raw(config_content, f"program:{PROGRAM_NAME}"):
        print("enrichment_dispatcher_daemon already wired to supervisord")
        sys.exit(0)

    # 3. Create backup with UTC timestamp
    backup_path = f"{CONFIG_PATH}.bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    shutil.copy2(CONFIG_PATH, backup_path)
    print(f"Backup created: {backup_path}")

    # 4. Append the program section
    with open(CONFIG_PATH, "a") as f:
        f.write("\n")
        f.write(PROGRAM_SECTION)

    # 5. Verify the file parses as valid ini
    with open(CONFIG_PATH, "r") as f:
        verify_content = f.read()

    if not has_section_raw(verify_content, f"program:{PROGRAM_NAME}"):
        print(f"ERROR: Missing section [program:{PROGRAM_NAME}] after write", file=sys.stderr)
        sys.exit(1)

    # Also verify with configparser (using delimiters that work with supervisord format)
    verify_config = configparser.ConfigParser(delimiters=('=',), comment_prefixes=('#', ';'))
    try:
        verify_config.read_string(verify_content)
        print("enrichment_dispatcher_daemon wired to supervisord")
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: Config validation failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
