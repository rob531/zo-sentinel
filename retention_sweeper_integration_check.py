#!/usr/bin/env python3
"""
retention_sweeper_integration_check.py
Verifies retention_sweeper.py wiring and nightly trigger from assessment_scheduler.

Checks:
 1. retention_sweeper.py exists on disk
 2. retention_sweeper is registered in supervisord configs
 3. retention_sweeper queries evidence_blob columns with age >30 days (code review)
 4. assessment_scheduler triggers retention_sweeper nightly
 5. Daemon health status from service_health table
"""
import os
import sys
import re
from pathlib import Path

REPO_ROOT = Path("/home/workspace/zo_sentinel")
SUPERVISORD_CONFIGS = [
    REPO_ROOT / "supervisord.conf",
    REPO_ROOT / "supervisord-user.conf",
    REPO_ROOT / "supervisord_sentinel.conf",
    REPO_ROOT / "supervisord_sentinel_full.conf",
    REPO_ROOT / "supervisord_phase8_update.conf",
    Path("/etc/zo/supervisord-user.conf"),
]

RETENTION_SWEEPER_FILE = REPO_ROOT / "retention_sweeper.py"
ASSESSMENT_SCHEDULER_FILE = REPO_ROOT / "assessment_scheduler.py"


def check_file_exists(path: Path) -> bool:
    return path.exists()


def grep_retention_in_supervisord():
    """Search all supervisord configs for retention_sweeper."""
    found_in = []
    for conf in SUPERVISORD_CONFIGS:
        if conf.exists():
            content = conf.read_text()
            if "retention_sweeper" in content.lower():
                found_in.append(str(conf))
        # Also scan directory for any conf with retention_sweeper
    # Broader scan
    for conf in REPO_ROOT.glob("**/*.conf"):
        if conf.is_file():
            try:
                if "retention_sweeper" in conf.read_text().lower():
                    found_in.append(str(conf))
            except Exception:
                pass
    return list(dict.fromkeys(found_in))  # dedupe order-preserving


def grep_retention_trigger_in_scheduler():
    """Check if assessment_scheduler.py calls/triggers retention_sweeper."""
    if not ASSESSMENT_SCHEDULER_FILE.exists():
        return []
    content = ASSESSMENT_SCHEDULER_FILE.read_text()
    hits = []
    for line_no, line in enumerate(content.splitlines(), 1):
        if "retention" in line.lower():
            hits.append((line_no, line.strip()))
    return hits


def check_30day_evidence_blob_logic():
    """Spot-check retention_sweeper.py for >30 day evidence_blob expiry logic."""
    if not RETENTION_SWEEPER_FILE.exists():
        return None
    content = RETENTION_SWEEPER_FILE.read_text()
    checks = {
        "RETENTION_DAYS": re.search(r"RETENTION_DAYS\s*=\s*(\d+)", content),
        "30_day_cutoff": re.search(r"(30\s*days?|RETENTION_DAYS)", content),
        "evidence_blob_col": re.search(r"(evidence_blob|evidence)", content, re.IGNORECASE),
        "timestamp_filter": re.search(r"(scored_at|created_at)\s*<\s*.*(now|cutoff)", content, re.IGNORECASE),
    }
    return {k: bool(v) for k, v in checks.items()}


def main():
    print("=" * 60)
    print("retention_sweeper_integration_check")
    print("=" * 60)

    results = {}

    # 1. File existence
    print("\n[1] File existence check")
    sweeper_exists = check_file_exists(RETENTION_SWEEPER_FILE)
    print(f"  retention_sweeper.py exists: {sweeper_exists}")
    scheduler_exists = check_file_exists(ASSESSMENT_SCHEDULER_FILE)
    print(f"  assessment_scheduler.py exists: {scheduler_exists}")
    results["sweeper_exists"] = sweeper_exists
    results["scheduler_exists"] = scheduler_exists

    # 2. Supervisord wiring
    print("\n[2] Supervisord wiring")
    wired_confs = grep_retention_in_supervisord()
    if wired_confs:
        print(f"  FOUND in: {', '.join(wired_confs)}")
        results["supervisord_wired"] = True
    else:
        print("  NOT FOUND in any supervisord config")
        results["supervisord_wired"] = False

    # 3. 30-day evidence_blob logic
    print("\n[3] 30-day evidence_blob retention logic")
    logic = check_30day_evidence_blob_logic()
    if logic is None:
        print("  retention_sweeper.py not found; cannot verify logic")
        results["retention_logic_ok"] = False
    else:
        print(f"  RETENTION_DAYS constant: {logic.get('RETENTION_DAYS')}")
        print(f"  30-day cutoff references: {logic.get('30_day_cutoff')}")
        print(f"  evidence_blob/evidence column refs: {logic.get('evidence_blob_col')}")
        print(f"  timestamp-based filtering: {logic.get('timestamp_filter')}")
        results["retention_logic_ok"] = all(logic.values())
        if not results["retention_logic_ok"]:
            print("  WARNING: Some retention logic checks did not pass")

    # 4. Nightly trigger from assessment_scheduler
    print("\n[4] Nightly trigger: assessment_scheduler -> retention_sweeper")
    trigger_hits = grep_retention_trigger_in_scheduler()
    if trigger_hits:
        print("  FOUND retention references in assessment_scheduler.py:")
        for line_no, line in trigger_hits:
            print(f"    line {line_no}: {line}")
        results["scheduler_triggers_retention"] = True
    else:
        print("  NOT FOUND: assessment_scheduler.py does not reference retention_sweeper")
        print("  NOTE: retention_sweeper.run() has its own internal 86400s sweep cycle,")
        print("        so nightly triggering from assessment_scheduler is NOT wired.")
        results["scheduler_triggers_retention"] = False

    # 5. Daemon health status
    print("\n[5] Daemon health status (from service_health table)")
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT service, last_heartbeat FROM service_health WHERE service IN ('retention_sweeper', 'assessment_scheduler')"},
            timeout=10
        )
        rows = resp.json().get("rows", [])
        if rows:
            for row in rows:
                hb = row.get("last_heartbeat", "N/A")
                print(f"  {row['service']}: last_heartbeat={hb}")
                results[f"health_{row['service']}"] = hb
        else:
            print("  No heartbeat records found for either daemon")
            results["health_retention_sweeper"] = None
            results["health_assessment_scheduler"] = None
    except Exception as e:
        print(f"  Could not query service_health: {e}")
        results["health_query_error"] = str(e)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    issues = []
    if not sweeper_exists:
        issues.append("retention_sweeper.py missing from disk")
    if not results.get("supervisord_wired"):
        issues.append("retention_sweeper NOT wired in supervisord")
    if not results.get("retention_logic_ok"):
        issues.append("30-day evidence_blob retention logic issue")
    if not results.get("scheduler_triggers_retention"):
        issues.append("assessment_scheduler does NOT trigger retention_sweeper nightly")

    if issues:
        print("  ISSUES FOUND:")
        for issue in issues:
            print(f"    - {issue}")
        return 1
    else:
        print("  All checks PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
