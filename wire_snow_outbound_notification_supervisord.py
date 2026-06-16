#!/usr/bin/env python3
"""
Wire snow_connector.py and aidr_commit_gateway.py into supervisord_sentinel_full.conf.
Phase 9 integration wiring.

Reads the current conf, checks whether [program:snow_connector] and
[program:aidr_commit_gateway] entries exist, and if not, appends them.
Idempotent: re-runs produce identical output.
"""

import os
import shutil
from datetime import datetime, timezone

CONF_PATH = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"
SNOW_PROGRAM = "[program:snow_connector]"
AIDR_PROGRAM = "[program:aidr_commit_gateway]"
SNOW_CMD = "command=python3 /home/workspace/zo_sentinel/snow_connector.py"
AIDR_CMD = "command=python3 /home/workspace/zo_sentinel/aidr_commit_gateway.py"


def utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def entry_snow():
    return f"""\
[program:snow_connector]
{SNOW_CMD}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=5
user=workspace
stdout_logfile=/home/workspace/logs/sentinel_snow_connector.log
stderr_logfile=/home/workspace/logs/sentinel_snow_connector.log
stdout_logfile_maxbytes=5MB
"""


def entry_aidr():
    return f"""\
[program:aidr_commit_gateway]
{AIDR_CMD}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=5
user=workspace
stdout_logfile=/home/workspace/logs/sentinel_aidr_commit_gateway.log
stderr_logfile=/home/workspace/logs/sentinel_aidr_commit_gateway.log
stdout_logfile_maxbytes=5MB
"""


def has_program(content: str, program: str) -> bool:
    """Check if a [program:...] entry already exists in the conf content."""
    return program in content


def run():
    """Read conf, add entries if missing, write back with backup. Idempotent."""
    if not os.path.exists(CONF_PATH):
        raise FileNotFoundError(f"Conf not found: {CONF_PATH}")

    # Read current content
    with open(CONF_PATH, "r") as f:
        content = f.read()

    modified = False

    if not has_program(content, SNOW_PROGRAM):
        content += "\n" + entry_snow()
        modified = True

    if not has_program(content, AIDR_PROGRAM):
        content += "\n" + entry_aidr()
        modified = True

    if modified:
        # Backup before writing
        bak_path = f"{CONF_PATH}.bak.{utc_timestamp()}"
        shutil.copy2(CONF_PATH, bak_path)
        with open(CONF_PATH, "w") as f:
            f.write(content)

    return content


def verify(content: str):
    """Assert both programs are present exactly once."""
    assert has_program(content, SNOW_PROGRAM), "snow_connector entry missing"
    assert has_program(content, AIDR_PROGRAM), "aidr_commit_gateway entry missing"
    # Count occurrences to ensure no duplication
    assert content.count(SNOW_PROGRAM) == 1, f"Duplicated snow_connector: {content.count(SNOW_PROGRAM)}"
    assert content.count(AIDR_PROGRAM) == 1, f"Duplicated aidr_commit_gateway: {content.count(AIDR_PROGRAM)}"


if __name__ == "__main__":
    # First run
    result1 = run()
    verify(result1)

    # Second run -- must be identical (idempotent)
    result2 = run()
    verify(result2)
    assert result1 == result2, "Second run produced different content (not idempotent)"

    print("PASS")
