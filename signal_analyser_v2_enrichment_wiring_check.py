import os
import re
import sys
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

SERVICE_NAME = "signal_analyser_v2_enrichment_wiring_check"
PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"

SIGNAL_ANALYSER_PATH = "/home/workspace/zo_sentinel/signal_analyser_v2.py"
ENRICHMENT_DIR = "/home/workspace/zo_sentinel"

REQUIRED_ENRICHERS = [
    "permission_scope_enrichment_v2",
    "tool_description_safety_enrichment_v2",
    "supply_chain_enrichment",
    "community_signal_enrichment",
    "domain_trust_enrichment",
]

log = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(status: str = "running", meta: dict = None):
    meta = meta or {}
    row = {
        "service": SERVICE_NAME,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
        "meta": str(meta),
    }
    ws_write("service_health", [row])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_source_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as e:
        log.warning(f"Could not read {path}: {e}")
        return ""


def find_imports_in_source(source: str) -> dict:
    """Find import statements referencing enrichment modules."""
    found = {}
    for enricher in REQUIRED_ENRICHERS:
        pattern = rf"import\s+{re.escape(enricher)}|from\s+{re.escape(enricher)}\s+import"
        if re.search(pattern, source):
            found[enricher] = True
    return found


def find_compute_score_calls_in_source(source: str) -> dict:
    """Find compute_score calls referencing enrichment modules."""
    found = {}
    for enricher in REQUIRED_ENRICHERS:
        pattern = rf"{re.escape(enricher)}.*compute_score|compute_score.*from.*{re.escape(enricher)}"
        if re.search(pattern, source, re.IGNORECASE):
            found[enricher] = True
    return found


def check_module_on_disk(enricher: str) -> bool:
    """Check if enrichment module file exists on disk."""
    path = Path(ENRICHMENT_DIR) / f"{enricher}.py"
    return path.exists()


def query_enrichment_rows_7d(signal_type: str) -> int:
    """Count rows in mcp_signal_enrichments for signal_type in last 7 days."""
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    sql = f"SELECT COUNT(*) as cnt FROM mcp_signal_enrichments WHERE signal_type = '{signal_type}' AND computed_at >= '{seven_days_ago}'"
    rows = ws_query(sql)
    if rows:
        return rows[0].get("cnt", 0) or 0
    return 0


def query_signal_scores_rows_7d(signal_name: str) -> int:
    """Count rows in mcp_signal_scores for signal_name in last 7 days."""
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    sql = f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE signal_name = '{signal_name}' AND computed_at >= '{seven_days_ago}'"
    rows = ws_query(sql)
    if rows:
        return rows[0].get("cnt", 0) or 0
    return 0


def map_enricher_to_signal_name(enricher: str) -> str:
    """Map enricher module name to signal_name used in mcp_signal_scores."""
    mapping = {
        "permission_scope_enrichment_v2": "permission_scope",
        "tool_description_safety_enrichment_v2": "tool_description_safety",
        "supply_chain_enrichment": "supply_chain",
        "community_signal_enrichment": "community_signal",
        "domain_trust_enrichment": "domain_trust",
    }
    return mapping.get(enricher, enricher)


def map_enricher_to_signal_type(enricher: str) -> str:
    """Map enricher module name to signal_type used in mcp_signal_enrichments."""
    return map_enricher_to_signal_name(enricher)


def audit_signal_analyser() -> dict:
    """Read signal_analyser_v2.py source and check enrichment wiring."""
    source = read_source_file(SIGNAL_ANALYSER_PATH)
    if not source:
        return {"error": f"Could not read {SIGNAL_ANALYSER_PATH}"}

    imports_found = find_imports_in_source(source)
    compute_calls_found = find_compute_score_calls_in_source(source)
    disk_modules = {e: check_module_on_disk(e) for e in REQUIRED_ENRICHERS}

    result = {}
    for enricher in REQUIRED_ENRICHERS:
        signal_name = map_enricher_to_signal_name(enricher)
        signal_type = map_enricher_to_signal_type(enricher)

        enrich_rows_7d = query_enrichment_rows_7d(signal_type)
        score_rows_7d = query_signal_scores_rows_7d(signal_name)

        result[enricher] = {
            "on_disk": disk_modules.get(enricher, False),
            "imported_in_analyser": imports_found.get(enricher, False),
            "compute_score_in_analyser": compute_calls_found.get(enricher, False),
            "enrichment_rows_7d": enrich_rows_7d,
            "signal_scores_rows_7d": score_rows_7d,
            "wired_but_silent": (
                imports_found.get(enricher, False) and
                disk_modules.get(enricher, False) and
                enrich_rows_7d == 0 and
                score_rows_7d == 0
            ),
        }
    return result


def print_audit_report(audit: dict):
    print(f"\n{'='*70}")
    print(f"SIGNAL ANALYSER v2 ENRICHMENT WIRING AUDIT")
    print(f"Generated: {utc_now_iso()}")
    print(f"{'='*70}\n")

    silent_enrichers = []
    for enricher, info in audit.items():
        if "error" in info:
            print(f"[ERROR] {enricher}: {info['error']}")
            continue

        status_icon = "OK" if not info["wired_but_silent"] else "SILENT"
        print(f"[{status_icon}] {enricher}")
        print(f"  on_disk:                     {info['on_disk']}")
        print(f"  imported_in_analyser:        {info['imported_in_analyser']}")
        print(f"  compute_score_in_analyser:   {info['compute_score_in_analyser']}")
        print(f"  mcp_signal_enrichments rows (7d): {info['enrichment_rows_7d']}")
        print(f"  mcp_signal_scores rows (7d):      {info['signal_scores_rows_7d']}")

        if info["wired_but_silent"]:
            print(f"  *** PROBLEM: Wired but producing no data in 7 days ***")
            silent_enrichers.append(enricher)
        print()

    print(f"{'='*70}")
    if silent_enrichers:
        print(f"SILENT ENRICHERS ({len(silent_enrichers)}): {', '.join(silent_enrichers)}")
    else:
        print("All enrichers are active and producing data.")
    print(f"{'='*70}\n")


def persist_diagnostic(audit: dict):
    """Write diagnostic result to service_health meta for ManagerAgent visibility."""
    silent = [e for e, info in audit.items() if info.get("wired_but_silent")]
    meta = {
        "enrichers_checked": len(audit),
        "silent_count": len(silent),
        "silent_enrichers": silent,
        "utc": utc_now_iso(),
    }
    send_heartbeat(status="running", meta=meta)


def cycle():
    audit = audit_signal_analyser()
    print_audit_report(audit)
    persist_diagnostic(audit)
    return audit


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"[FATAL] {SERVICE_NAME} already running as PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    log.info(f"{SERVICE_NAME} starting")
    cycle()
    remove_pid_file()
    sys.exit(0)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE)],
    )
    run()