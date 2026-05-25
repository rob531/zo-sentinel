#!/usr/bin/env python3
"""
sentinel_directive_generator.py

A dedicated intent engine for ZO-SENTINEL. Unlike the general-purpose
childofintent engine (which reads global mesh logs), this generator:

  1. Reads SENTINEL_DIRECTIVE_SCHEMA.md -- deep ZO Sentinel knowledge
  2. Reads live build state: .build_registry.json, BUILD_STATE.md
  3. Reads recent build failures from mesh_memory
  4. Constructs a rich prompt combining all three
  5. Calls the InferenceRouter (or Anthropic) to generate directive suggestions
  6. Writes valid directive JSON files to directives/
  7. Stores a record in mesh_memory

Runs every 2 hours. Only generates directives when queue is empty or near-empty.
Produces 3-8 targeted directives per run, ordered by priority.

Start:
  nohup python3 /home/workspace/zo_sentinel/sentinel_directive_generator.py \\
    >> /home/workspace/logs/sentinel_directive_gen.log 2>&1 &
"""
import os, json, time, logging, requests, hashlib
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE   = "http://127.0.0.1:8772"
INFERENCE_URL   = "http://127.0.0.1:8773/complete"
OLLAMA_URL      = "http://127.0.0.1:11434/api/generate"
PROJECT_DIR     = Path("/home/workspace/zo_sentinel")
DIRECTIVE_DIR   = PROJECT_DIR / "directives"
SCHEMA_PATH     = PROJECT_DIR / "SENTINEL_DIRECTIVE_SCHEMA.md"
REGISTRY_PATH   = PROJECT_DIR / ".build_registry.json"
BUILD_STATE_PATH= PROJECT_DIR / "BUILD_STATE.md"
SERVICE_NAME    = "sentinel_directive_generator"
POLL_SECS       = 7200   # 2 hours
MIN_QUEUE_TO_SKIP = 5    # skip generation if >5 directives already queued
MAX_DIRECTIVES  = 8      # max to generate per run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [directive_gen] %(levelname)s: %(message)s"
)
log = logging.getLogger(SERVICE_NAME)
DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)


# ── Write Service Helpers ───────────────────────────────────────────────────────────

def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=8)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.warning("ws_query: %s", e)
    return []

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": row, "wait": True}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def heartbeat():
    ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat()
    })


# ── Context Builders ────────────────────────────────────────────────────────────────

def load_schema() -> str:
    if SCHEMA_PATH.exists():
        return SCHEMA_PATH.read_text()
    return "# Schema not found"

def get_failed_modules() -> list[str]:
    """Return task names with failed/smoke_fail status from registry."""
    if not REGISTRY_PATH.exists():
        return []
    try:
        reg = json.loads(REGISTRY_PATH.read_text())
        return [
            v["task"] for v in reg.values()
            if v.get("status") in ("failed", "smoke_fail")
            and v.get("task", "").startswith("build_")
        ]
    except Exception:
        return []

def get_queue_depth() -> int:
    """Count active (non-done) directive files."""
    return len([f for f in DIRECTIVE_DIR.glob("*.json")
                if ".done." not in f.name])

def get_recent_build_failures() -> str:
    """Recent smoke_fail records from mesh_memory."""
    rows = ws_query(
        "SELECT content FROM mesh_memory "
        "WHERE agent_id='zo_sentinel.smoke_fail' "
        "ORDER BY created_at DESC LIMIT 5"
    )
    if not rows:
        return "No recent failures recorded."
    summaries = []
    for row in rows:
        try:
            c = json.loads(row.get("content", "{}"))
            summaries.append(f"  - {c.get('filename','?')}: {c.get('traceback_tail','')[:100]}")
        except Exception:
            pass
    return "\n".join(summaries) if summaries else "None."

def get_registry_summary() -> str:
    """Brief summary of what's built vs failed."""
    if not REGISTRY_PATH.exists():
        return "Registry not available."
    try:
        reg = json.loads(REGISTRY_PATH.read_text())
        ok    = sum(1 for v in reg.values() if v.get("status") == "ok")
        fail  = sum(1 for v in reg.values() if v.get("status") == "failed")
        smoke = sum(1 for v in reg.values() if v.get("status") == "smoke_fail")
        return f"{ok} built OK, {fail} failed, {smoke} smoke_fail"
    except Exception:
        return "Registry read error."


# ── LLM Generation ───────────────────────────────────────────────────────────────────

def build_prompt(schema: str, failed: list, failures_detail: str,
                registry_summary: str, queue_depth: int) -> str:
    failed_str = "\n".join(f"  - {t}" for t in failed[:10]) or "  None"
    return f"""You are the Sentinel Directive Generator for ZO-SENTINEL.

Your job: analyze the current build state and generate a JSON array of
between 3 and {MAX_DIRECTIVES} builder directives for the next build cycle.

## Current Build State
Registry summary: {registry_summary}
Current directive queue depth: {queue_depth}
Failed modules (need retry or replacement):
{failed_str}

Recent smoke failures:
{failures_detail}

## ZO-SENTINEL Knowledge Base
{schema}

## Instructions
Generate a JSON array of directive objects. Each directive MUST follow the
exact schema defined in the Knowledge Base above. Order by priority (highest first).
Focus on:
  1. Failed modules that need a fresh attempt now that MiniMax is available
  2. Missing modules from the 'What Is MISSING' section
  3. Quality passes for hollow stubs
  4. Integration glue between existing modules

Return ONLY a valid JSON array. No explanation, no markdown fences, no preamble.
Example output format:
[
  {{"task": "rebuild_email_guid_auth", "handler": "generate_file", 
    "output_file": "email_guid_auth.py", "complexity": "medium",
    "phase": "11", "priority": 0.9,
    "description": "..."}},
  ...
]
"""

def call_minimax(prompt: str) -> str:
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return ""
    try:
        r = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": "MiniMax-M2.7",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 4096},
            timeout=120
        )
        if r.status_code == 200:
            choices = r.json().get("choices", [])
            return choices[0].get("message", {}).get("content", "").strip() if choices else ""
    except Exception as e:
        log.warning("MiniMax call failed: %s", e)
    return ""

def call_ollama(prompt: str) -> str:
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": "llama3.2:3b",
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": 8192}
        }, timeout=120)
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except Exception as e:
        log.warning("Ollama call failed: %s", e)
    return ""

def generate_directives(prompt: str) -> list:
    """Call LLM, parse JSON array of directives."""
    # Try MiniMax first (better JSON output), fall back to Ollama
    raw = call_minimax(prompt)
    if not raw:
        log.info("MiniMax unavailable, trying Ollama")
        raw = call_ollama(prompt)
    if not raw:
        log.warning("No LLM response")
        return []

    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Find JSON array
    start = text.find("[")
    end   = text.rfind("]") + 1
    if start == -1 or end == 0:
        log.warning("No JSON array in response")
        return []

    try:
        directives = json.loads(text[start:end])
        if isinstance(directives, list):
            return directives
    except json.JSONDecodeError as e:
        log.warning("JSON parse error: %s", e)
    return []


# ── Directive Validation & Writing ────────────────────────────────────────────────

REQUIRED_FIELDS = {"task", "handler", "output_file", "description"}
VALID_HANDLERS  = {"generate_file", "write_raw", "run_script"}
VALID_COMPLEXITY = {"low", "medium", "high"}

# Files that already exist and must not be re-queued
ALREADY_BUILT = {
    "schema.py", "schema_v2.py", "mcp_scanner.py", "known_threats.py",
    "signal_analyser.py", "trust_synthesiser.py", "approval_workflow.py",
    "policy_engine.py", "rug_pull_monitor.py", "registry_api.py",
    "threat_intel_ingestor.py", "risk_ranker.py", "attestation_engine.py",
    "search_api.py", "lookup.py", "pipeline_health.py", "integration_test.py",
    "rate_limiter.py", "error_reporter.py", "config_validator.py",
    "url_analyser.py", "text_patterns.py", "db_utils.py", "http_retry.py",
    "watch.py", "sentinel_cli.py", "sentinel_sdk.py", "audit_trail.py",
    "dashboard_api.py", "daily_digest.py", "trend_analyser.py",
}

def validate_directive(d: dict) -> tuple[bool, str]:
    if not isinstance(d, dict):
        return False, "not a dict"
    missing = REQUIRED_FIELDS - d.keys()
    if missing:
        return False, f"missing fields: {missing}"
    if d.get("handler") not in VALID_HANDLERS:
        return False, f"invalid handler: {d.get('handler')}"
    if d.get("complexity") and d["complexity"] not in VALID_COMPLEXITY:
        return False, f"invalid complexity: {d.get('complexity')}"
    output = d.get("output_file", "")
    if output in ALREADY_BUILT:
        return False, f"already built: {output}"
    if len(d.get("description", "")) < 50:
        return False, "description too short (<50 chars)"
    return True, "ok"

def already_queued(task: str) -> bool:
    return bool(list(DIRECTIVE_DIR.glob(f"*{task[:30]}*.json")))

def write_directive(d: dict, index: int) -> bool:
    task = d.get("task", f"gen_{index}")
    key  = hashlib.md5(task.encode()).hexdigest()[:8]
    path = DIRECTIVE_DIR / f"gen_{key}_{task[:35]}.json"
    try:
        path.write_text(json.dumps(d, indent=2))
        log.info("  Written directive: %s -> %s", task, d.get("output_file", "?"))
        return True
    except Exception as e:
        log.error("  Write failed: %s", e)
        return False


# ── Main Cycle ─────────────────────────────────────────────────────────────────────────

def run_cycle():
    heartbeat()
    queue = get_queue_depth()
    log.info("Queue depth: %d", queue)

    if queue >= MIN_QUEUE_TO_SKIP:
        log.info("Queue has %d directives, skipping generation", queue)
        return

    log.info("Queue low (%d), generating new directives...", queue)

    schema          = load_schema()
    failed          = get_failed_modules()
    failures_detail = get_recent_build_failures()
    reg_summary     = get_registry_summary()

    prompt = build_prompt(schema, failed, failures_detail, reg_summary, queue)
    log.info("Prompt: %d chars", len(prompt))

    directives = generate_directives(prompt)
    if not directives:
        log.warning("LLM returned no directives")
        return

    log.info("LLM suggested %d directives", len(directives))
    written = 0
    for i, d in enumerate(directives[:MAX_DIRECTIVES]):
        valid, reason = validate_directive(d)
        if not valid:
            log.warning("  Skip [%d]: %s", i, reason)
            continue
        if already_queued(d["task"]):
            log.info("  Skip (already queued): %s", d["task"])
            continue
        if write_directive(d, i):
            written += 1

    log.info("Generation complete: %d/%d directives written", written, len(directives))

    # Record to mesh memory
    ws_write("mesh_memory", {
        "agent_id": SERVICE_NAME,
        "memory_type": "directive_generation",
        "content": json.dumps({
            "directives_written": written,
            "queue_before": queue,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }),
        "importance": 0.7,
        "created_at": datetime.now(timezone.utc).isoformat()
    })


def run():
    log.info("=" * 60)
    log.info("ZO-SENTINEL Directive Generator v1.0")
    log.info("  Schema: %s", SCHEMA_PATH)
    log.info("  Poll:   every %ds", POLL_SECS)
    log.info("  Skip if queue >= %d directives", MIN_QUEUE_TO_SKIP)
    log.info("  MiniMax: %s", "SET" if os.environ.get("MINIMAX_API_KEY") else "NOT SET (Ollama fallback)")
    log.info("=" * 60)

    # Run immediately on start
    try:
        run_cycle()
    except Exception as e:
        log.error("Initial cycle: %s", e)

    while True:
        time.sleep(POLL_SECS)
        try:
            run_cycle()
        except Exception as e:
            log.error("Cycle error: %s", e)


if __name__ == "__main__":
    run()