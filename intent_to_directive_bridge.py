#!/usr/bin/env python3
"""
intent_to_directive_bridge.py

Bridge: Intent Engine anticipations -> ZO-SENTINEL builder directives.

Runs as a daemon (every 15 min). Reads high/critical urgency anticipations
from the anticipations table, filters for sentinel/security/build domain,
translates them into directive JSON files that the builder picks up.

Also creates the anticipations table if it doesn't exist.

This makes the intent engine the creative director of the builder:
  Intent Engine observes ZOMesh -> generates anticipations
  This bridge reads high-urgency ones -> writes builder directives
  Builder picks them up -> builds what the system needs
"""
import json, time, logging, requests, hashlib
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE  = "http://127.0.0.1:8772"
DIRECTIVE_DIR  = Path("/home/workspace/zo_sentinel/directives")
SERVICE_NAME   = "intent_to_directive_bridge"
POLL_SECS      = 900  # 15 min

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [intent_bridge] %(levelname)s: %(message)s")
log = logging.getLogger(SERVICE_NAME)

DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)


def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.warning("ws_query: %s", e)
    return []

def ws_execute(sql: str) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/execute", json={"sql": sql}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": row, "wait": True}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def ensure_anticipations_table():
    """Create anticipations table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS anticipations (
        id              BIGINT PRIMARY KEY DEFAULT (epoch_ms(now())),
        domain          VARCHAR,
        pattern_detected VARCHAR,
        anticipated_need VARCHAR,
        suggested_action VARCHAR,
        urgency         VARCHAR,
        status          VARCHAR DEFAULT 'pending',
        directive_written BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMPTZ DEFAULT now()
    )
    """
    ok = ws_execute(sql.strip())
    if ok:
        log.info("anticipations table ready")
    else:
        log.warning("Could not create anticipations table")
    return ok


# Domains and keywords that map to sentinel build directives
SENTINEL_DOMAINS = {"security", "mcp", "sentinel", "build", "infosec", "threat"}

COMPLEXITY_MAP = {
    "critical": "high",
    "high":     "medium",
    "medium":   "low",
    "low":      "low",
}

def anticipation_to_directive(a: dict) -> dict | None:
    """Convert an anticipation to a builder directive, or None if not applicable."""
    domain    = (a.get("domain") or "").lower()
    urgency   = (a.get("urgency") or "low").lower()
    action    = a.get("suggested_action") or ""
    need      = a.get("anticipated_need") or ""

    # Only act on security/sentinel domain anticipations
    if not any(kw in domain for kw in SENTINEL_DOMAINS):
        return None
    if not any(kw in (action + need).lower() for kw in
               ["build", "create", "add", "implement", "generate", "scan", "monitor", "api"]):
        return None

    # Generate a stable task name from the action
    task_slug = action.lower()[:50]
    for ch in " /\\:.,;!?'\"":
        task_slug = task_slug.replace(ch, "_")
    task_slug = "intent_" + task_slug.strip("_")[:40]

    # Derive output filename
    output_file = task_slug.replace("intent_", "").replace("build_", "").replace("create_", "")
    if not output_file.endswith(".py"):
        output_file = output_file[:30] + ".py"

    complexity = COMPLEXITY_MAP.get(urgency, "medium")
    priority   = {"critical": 0.95, "high": 0.85, "medium": 0.70, "low": 0.55}.get(urgency, 0.70)

    return {
        "task":        task_slug,
        "handler":     "generate_file",
        "output_file": output_file,
        "complexity":  complexity,
        "phase":       "intent",
        "priority":    priority,
        "description": f"{need}. {action}. Generated from intent engine anticipation (urgency={urgency}).",
        "from":        "intent_engine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def directive_key(task: str) -> str:
    return hashlib.md5(task.encode()).hexdigest()[:12]


def process_anticipations():
    rows = ws_query(
        "SELECT id, domain, pattern_detected, anticipated_need, "
        "suggested_action, urgency FROM anticipations "
        "WHERE status = 'pending' AND directive_written = FALSE "
        "AND urgency IN ('critical', 'high') "
        "ORDER BY created_at DESC LIMIT 10"
    )

    if not rows:
        log.info("No pending high-urgency anticipations")
        return 0

    written = 0
    for row in rows:
        directive = anticipation_to_directive(row)
        if not directive:
            log.info("  Skipped (non-sentinel domain): %s", row.get("domain"))
            continue

        # Check if directive file already exists
        existing = list(DIRECTIVE_DIR.glob(f"*{directive['task']}*.json"))
        if existing:
            log.info("  Already queued: %s", directive["task"])
            continue

        # Write directive file
        key  = directive_key(directive["task"])
        path = DIRECTIVE_DIR / f"intent_{key}_{directive['task'][:30]}.json"
        path.write_text(json.dumps(directive, indent=2))
        log.info("  Directive written: %s -> %s", directive["task"], directive["output_file"])

        # Mark anticipation as actioned
        ws_execute(
            f"UPDATE anticipations SET directive_written = TRUE, status = 'actioned' "
            f"WHERE id = {row.get('id')}"
        )
        written += 1

    if written:
        log.info("Wrote %d directive(s) from intent engine", written)
    return written


def heartbeat():
    ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat()
    })


def run():
    log.info("Intent->Directive Bridge starting (poll every %ds)", POLL_SECS)
    ensure_anticipations_table()
    heartbeat()
    while True:
        try:
            process_anticipations()
            heartbeat()
        except Exception as e:
            log.error("Cycle error: %s", e)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()