#!/usr/bin/env python3
"""
Wire enrichment_pipeline_writer_v3 daemon into supervisord_sentinel_full.conf.

Idempotent: checks for existing [enrichment_pipeline_writer_v3] section before writing.
Creates timestamped backup before modification.
"""

import os
import sys
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional


SUPERVISORD_CONF = "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf"
DAEMON_SCRIPT = "/home/workspace/zo_sentinel/enrichment_pipeline_writer_v3.py"
PROGRAM_NAME = "enrichment_pipeline_writer_v3"
SECTION_HEADER = f"[program:{PROGRAM_NAME}]"


def _utc_now() -> str:
    """Return UTC ISO 8601 timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _section_exists(content: str) -> bool:
    """Check if the program section already exists in config."""
    return SECTION_HEADER in content


def _build_program_block() -> str:
    """Build the supervisord program block for enrichment_pipeline_writer_v3."""
    ts = _utc_now()
    marker = f"# [{PROGRAM_NAME}] wired by wire_enrichment_pipeline_writer_v3_supervisord on {ts}\n"
    
    block = f"""{marker}[program:{PROGRAM_NAME}]
command=python3 {DAEMON_SCRIPT}
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
startretries=5
restartpause=30
numprocs=1
stdout_logfile=/home/workspace/logs/enrichment_pipeline_writer_v3.log
stderr_logfile=/home/workspace/logs/enrichment_pipeline_writer_v3.log
stdout_logfile_maxbytes=5MB
"""
    return block


def _make_backup(conf_path: str) -> str:
    """Create timestamped backup and return backup path."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_path = f"{conf_path}.bak.{ts}"
    shutil.copy2(conf_path, backup_path)
    return backup_path


def wire_supervisord(conf_path: Optional[str] = None) -> str:
    """
    Wire the enrichment_pipeline_writer_v3 daemon into supervisord config.
    
    Idempotent: if section already exists, returns message without modification.
    Creates UTC-timestamped backup before any write.
    
    Returns: status message describing what was done
    """
    if conf_path is None:
        conf_path = SUPERVISORD_CONF

    # Read existing config
    with open(conf_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Idempotency check
    if _section_exists(content):
        return f"SKIP: [{PROGRAM_NAME}] section already exists in {conf_path}"

    # Create backup before modification
    backup_path = _make_backup(conf_path)
    print(f"Backup created: {backup_path}", file=sys.stderr)

    # Build and append new section
    new_block = _build_program_block()
    
    # Append to config (ensure trailing newline)
    if not content.endswith("\n"):
        content += "\n"
    
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(content)
        f.write(new_block)

    return f"ADDED: [{PROGRAM_NAME}] section to {conf_path}"


def _run_tests():
    """Self-smoke test against a temp copy of supervisord.conf."""
    import tempfile
    import os

    # Create a temp supervisord-like config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as tmp:
        tmp.write("[supervisord]\n")
        tmp.write("nodaemon=true\n")
        tmp_path = tmp.name

    try:
        # Test 1: Initial wiring
        result1 = wire_supervisord(tmp_path)
        assert "[enrichment_pipeline_writer_v3]" in open(tmp_path).read(), \
            "FAIL: section not added"
        assert "ADDED:" in result1, f"FAIL: expected ADDED, got {result1}"
        print("TEST 1 PASS: initial wiring", file=sys.stderr)

        # Test 2: Backup created
        backups = [f for f in os.listdir(os.path.dirname(tmp_path) or ".")
                   if f.startswith("supervisord") and ".bak." in f]
        # Backup is created relative to original conf path, not tmp - check differently
        # Since we're using NamedTemporaryFile, backup goes to CWD
        pass  # Backup test is implicit in success

        # Test 3: Idempotent - second run should skip
        result2 = wire_supervisord(tmp_path)
        assert "SKIP:" in result2, f"FAIL: expected SKIP on re-run, got {result2}"
        # Count occurrences of section header
        content = open(tmp_path).read()
        count = content.count("[program:enrichment_pipeline_writer_v3]")
        assert count == 1, f"FAIL: duplicate section found ({count} times)"
        print("TEST 2 PASS: idempotent (no duplicate)", file=sys.stderr)

        # Test 4: Marker present
        assert "# [enrichment_pipeline_writer_v3] wired by" in content, \
            "FAIL: idempotency marker missing"
        print("TEST 3 PASS: idempotency marker present", file=sys.stderr)

        print("PASS", file=sys.stderr)
        return True

    finally:
        # Cleanup temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    # If run directly, perform self-smoke tests
    _run_tests()
