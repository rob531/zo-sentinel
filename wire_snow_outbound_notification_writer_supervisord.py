#!/usr/bin/env python3
"""
Wiring directive: Add snow_outbound_notification_writer to supervisord_sentinel_full.conf.

REFUSAL STUB: The daemon file snow_outbound_notification_writer.py does not exist.
It must be built first before this wiring script can proceed.

PURPOSE: Ensure the ServiceNow outbound notification daemon auto-starts and heartbeats.
"""

import os
import sys

DAEMON_FILE = "/home/workspace/zo_sentinel/snow_outbound_notification_writer.py"
SUPERVISORD_CONF = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"

DAEMON_BLOCK = """[program:snow_outbound_notification_writer]
command=python3 /home/workspace/zo_sentinel/snow_outbound_notification_writer.py
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=log/snow_outbound_notification_writer.log
environment=PYTHONPATH="/home/workspace/zo_sentinel"
"""


def main():
    # REFUSAL: Check if daemon file exists - if not, emit refusal stub
    if not os.path.exists(DAEMON_FILE):
        print("REFUSAL: snow_outbound_notification_writer.py not found.")
        print("The daemon file must be built first before wiring.")
        print(f"Expected at: {DAEMON_FILE}")
        sys.exit(1)

    # Read existing supervisord config
    with open(SUPERVISORD_CONF, "r") as f:
        content = f.read()

    # Check if already wired (idempotent check)
    marker = "[program:snow_outbound_notification_writer]"
    if marker in content:
        # Extract existing block and compare
        existing_block_start = content.find(marker)
        remaining = content[existing_block_start:]
        next_block = remaining.find("\n[program:")
        if next_block == -1:
            existing_block = remaining.strip()
        else:
            existing_block = remaining[:next_block].strip()

        if existing_block == DAEMON_BLOCK.strip():
            print("Already wired: snow_outbound_notification_writer")
            sys.exit(0)

    # Append the daemon block
    with open(SUPERVISORD_CONF, "a") as f:
        f.write("\n" + DAEMON_BLOCK)

    print("Wired: snow_outbound_notification_writer")
    sys.exit(0)


if __name__ == "__main__":
    main()
