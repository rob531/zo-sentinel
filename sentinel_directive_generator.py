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
  8. (2026-04-27) If all LLM suggestions dedupe, falls through to
     standing_goals_fallback so the builder queue is NEVER empty.
  9. (2026-05-02 v1.2) already_queued() now excludes .done.json and
     .failed.json files. Previous version globbed *.json which matched
     completed work and forever-blocked re-emission of any task name that
     shared a 30-char prefix with a historical success. This caused an
     80-minute builder starvation today (2026-05-02 10:44 -> 12:04 UTC).

Runs every 45 minutes. Only generates directives when queue is empty or near-empty.
Produces 3-8 targeted directives per run, ordered by priority.

Start:
  nohup python3 /home/workspace/zo_sentinel/sentinel_directive_generator.py \\
    >> /home/workspace/logs/sentinel_directive_gen.log 2>&1 &
"""
import os, sys, json, time, logging, requests, hashlib, re
import threading
from datetime import datetime, timezone
from pathlib import Path

# Ensure zo_sentinel is on sys.path so sibling modules import cleanly even when
# the script is launched from a different cwd.
if '/home/workspace/zo_sentinel' not in sys.path:
    sys.path.insert(0, '/home/workspace/zo_sentinel')

import directive_knowledge_sources as dks  # Layer 1 (2026-04-18)
from http_retry import post_with_retry     # retry+backoff helper (2026-04-21)

# 2026-04-27: standing_goals fallback. Imported defensively so a missing module
# can't take down the generator (we just lose the never-empty guarantee).
try:
    from standing_goals_fallback import emit_standing_goals as _emit_standing_goals
except Exception as _e:
    _emit_standing_goals = None
    logging.getLogger("directive_gen").warning(
        "standing_goals_fallback unavailable: %s -- queue may go empty", _e
    )

WRITE_SERVICE   = "http://127.0.0.1:8772"
INFERENCE_URL   = "http://127.0.0.1:8773/complete"
OLLAMA_URL      = "http://127.0.0.1:11434/api/generate"
PROJECT_DIR     = Path("/home/workspace/zo_sentinel")
DIRECTIVE_DIR   = PROJECT_DIR / "directives"
SCHEMA_PATH     = PROJECT_DIR / "SENTINEL_DIRECTIVE_SCHEMA.md"
REGISTRY_PATH   = PROJECT_DIR / ".build_registry.json"
BUILD_STATE_PATH= PROJECT_DIR / "BUILD_STATE.md"
SERVICE_NAME    = "sentinel_directive_generator"
POLL_SECS       = 2700   # 45 minutes
MIN_QUEUE_TO_SKIP = 5    # skip generation if >5 directives already queued
MAX_DIRECTIVES  = 8      # max to generate per run
FALLBACK_MAX    = 3      # max standing-goal directives to emit per cycle

# Inference call budgets (2026-04-21: bumped from 120s due to MiniMax-M2.7
# reasoning mode pushing round-trip past the previous ceiling)
MINIMAX_TIMEOUT = 240
MINIMAX_RETRIES = 2      # total attempts = retries (post_with_retry semantics)
MINIMAX_BACKOFF = 2.0    # exponential: 2s, 4s between attempts
OLLAMA_TIMEOUT  = 240
OLLAMA_RETRIES  = 2
OLLAMA_BACKOFF  = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [directive_gen] %(levelname)s: %(message)s"
)
log = logging.getLogger(SERVICE_NAME)
DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)


# ── Write Service Helpers ───────────────────────────────────

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

_HEARTBEAT_INTERVAL = 30
_heartbeat_thread = None

def _heartbeat_loop():
    """Background thread: heartbeat every _HEARTBEAT_INTERVAL seconds.
    Independent of cycle state so liveness monitors always see fresh rows."""
    while True:
        try:
            heartbeat()
        except Exception as e:
            log.debug("heartbeat thread tick failed: %s", e)
        time.sleep(_HEARTBEAT_INTERVAL)

def _start_heartbeat_thread():
    """Start the heartbeat thread as a daemon. Safe to call more than once."""
    global _heartbeat_thread
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="heartbeat", daemon=True
    )
    _heartbeat_thread.start()
    log.info("Heartbeat thread started (interval=%ds)", _HEARTBEAT_INTERVAL)



# ── Context Builders ─────────────────────────────────────

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
    """Count active, BUILDABLE directive files. Spec-less reverse-feed directives
    (the ingestor's `fix_*` quarantine directives, origin=artifact_ingestion_quarantine)
    are NOT counted: they carry no buildable spec, so goose can't build them, and a
    flood of them would otherwise pin the queue at MIN_QUEUE_TO_SKIP and gag this
    generator forever -- the architect goes dormant while the queue is full of
    un-buildable churn (the 2026-06-03 drift). Quarantines are the breaker's job."""
    n = 0
    for f in DIRECTIVE_DIR.glob("*.json"):
        if ".done." in f.name:
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            n += 1   # unparseable -> count it (conservative; never under-count)
            continue
        if (str(d.get("directive_id", "")).startswith("fix_")
                or d.get("origin") == "artifact_ingestion_quarantine"
                or d.get("source") == "artifact_ingestor"):
            continue   # spec-less reverse-feed -> not buildable; don't let it gag us
        n += 1
    return n

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


# ── LLM Generation ───────────────────────────────────────


def get_signal_diversity_snapshot() -> str:
    """Live diagnostic of signal discrimination. Included in every prompt."""
    rows = ws_query(
        "SELECT signal_name, COUNT(DISTINCT score) AS distinct_vals, "
        "ROUND(MIN(score), 1) AS lo, ROUND(MAX(score), 1) AS hi "
        "FROM mcp_signal_scores "
        "GROUP BY signal_name ORDER BY distinct_vals DESC, signal_name"
    )
    if not rows:
        return "  (mcp_signal_scores empty -- no diagnostic available)"
    lines = ["  signal                       distinct   range           verdict"]
    for r in rows:
        sig = str(r.get("signal_name", "?"))[:28].ljust(28)
        dv = int(r.get("distinct_vals", 0))
        lo = r.get("lo", 0)
        hi = r.get("hi", 0)
        if dv == 1:
            verdict = "BAD -- flat, no discrimination"
            rng = (str(lo) + " flat").ljust(15)
        elif dv < 4:
            verdict = "WEAK -- low variety"
            rng = (str(lo) + " - " + str(hi)).ljust(15)
        else:
            verdict = "OK"
            rng = (str(lo) + " - " + str(hi)).ljust(15)
        lines.append("  " + sig + " " + str(dv).ljust(10) + " " + rng + " " + verdict)
    return chr(10).join(lines)


def build_prompt(schema: str, failed: list, failures_detail: str,
                registry_summary: str, queue_depth: int,
                layer1: dict | None = None) -> str:
    _nl = chr(10)
    failed_str = _nl.join(f"  - {t}" for t in failed[:10]) or "  None"
    diversity  = get_signal_diversity_snapshot()
    protected  = _nl.join(f"  - {f}" for f in sorted(PROTECTED_FILES))
    # Layer 1 knowledge sources (defensive defaults if missing)
    layer1       = layer1 or {}
    product_spec = layer1.get('product_spec', '[PRODUCT_SPEC unavailable]')
    wiring_map   = layer1.get('wiring_map',   '[wiring_map unavailable]')
    gaps_map     = layer1.get('gaps_map',     '[gaps_map unavailable]')
    quality_map  = layer1.get('quality_map',  '[quality_map unavailable]')
    return f"""You are the Sentinel Directive Generator for ZO-SENTINEL.

Your job: analyze the current build state and generate a JSON array of
between 3 and {MAX_DIRECTIVES} builder directives for the next cycle.

## Current Build State
Registry: {registry_summary}
Queue depth: {queue_depth}
Failed modules:
{failed_str}

Recent smoke failures:
{failures_detail}

## Signal Quality Diagnostic (live)

{diversity}

A signal is only useful if it discriminates between servers. When every
server gets the same score, that signal contributes nothing to the verdict.
The heuristic is: BAD SIGNAL = SAME SIGNAL across all inputs.

{product_spec}

{wiring_map}

{gaps_map}

{quality_map}

## How to use the four sections above

- PRODUCT_SPEC.md is the target v1.0 definition. Propose directives that
  close gaps IT lists. Do NOT propose items explicitly excluded from v1.0.
- The wiring map is a live snapshot of what exists and what heartbeats.
  Files in 'Recently built' are REAL — don't propose to rebuild them.
- The gaps map identifies concrete named files / daemons / tables that
  the spec asks for but the live system does not yet have. THESE ARE
  YOUR PRIMARY DIRECTIVE CANDIDATES.
- The quality map lists Gate 8 breaker state, quarantined files, and
  files currently under retry budget. Obey it. If the breaker state is
  'tripped', DO NOT propose rebuilds of any listed file. If a file is
  quarantined, NEVER propose a rebuild of it. If a file is under retry
  budget, you MAY propose a rebuild only if your description references
  the listed last_error and the relevant spec section explicitly.

## ZO-SENTINEL Knowledge Base
{schema}

## Instructions

Generate a JSON array. Order by priority. Focus areas:

  1. Failed modules that need a fresh attempt
  2. Missing modules from the 'What Is MISSING' section
  3. Quality passes for hollow stubs
  4. Integration glue between existing modules
  5. SIGNAL ENRICHMENT MODULES (highest-priority work right now)

## Signal Enrichment -- Strict Contract

Enrichment modules are NEW files named like '<signal_name>_enrichment.py'.
They are evaluated by an external harness (enrichment_harness.py) against
synthetic inputs, and gated by an evidence query before integration.

Each enrichment MUST:
  - Expose EXACTLY this function signature:
        def compute_score(metadata: dict) -> tuple[float, dict]
    which returns (score in [0.0, 100.0], evidence_dict).
  - Be a pure function. Same input always produces same output.
  - Read MULTIPLE metadata fields (registry_source, age_days, download_count,
    dependency_count, publisher_verified, stars). Reading only one field
    produces weak discrimination and will be rejected.
  - NOT write to the database. NOT import other project modules.
    NOT make network calls. NOT read files at runtime.
  - Complete each compute_score() call in under 2 seconds.
  - Return an evidence dict listing which fields it used and the partial
    scores derived from each (for auditability).

Example shape (supply_chain_enrichment.py):
    def compute_score(metadata: dict) -> tuple[float, dict]:
        score = 50.0
        evidence = {{}}
        if metadata.get("publisher_verified"):
            score += 20.0
            evidence["publisher_verified"] = 20
        age = metadata.get("age_days", 0)
        age_bonus = min(age / 30, 15)
        score += age_bonus
        evidence["age_bonus"] = age_bonus
        deps = metadata.get("dependency_count", 0)
        dep_penalty = min(deps * 0.5, 20)
        score -= dep_penalty
        evidence["dep_penalty"] = -dep_penalty
        score = max(0.0, min(100.0, score))
        return score, evidence

## Idempotency Protection (CRITICAL)

The following files are WORKING and protected. Any directive that targets
them will be REJECTED by the validator. To improve behaviour of these,
propose a NEW enrichment or companion module -- never a rewrite:

{protected}

Return ONLY a valid JSON array. No markdown fences, no preamble, no
<think> tags, no reasoning-mode output. Start your response with '[' and
end with ']'. Nothing outside the JSON array.

Example:
[
  {{"task": "build_supply_chain_enrichment", "handler": "generate_file",
    "output_file": "supply_chain_enrichment.py", "complexity": "medium",
    "phase": "12", "priority": 0.95,
    "description": "Pure enrichment module exposing compute_score(metadata) that returns (float, dict). Uses registry_source, age_days, download_count, dependency_count, publisher_verified, stars. No DB writes, no network, no imports of protected modules. Will be exercised by enrichment_harness.py and gated by enrichment_evidence.sql before integration."}}
]
"""


def call_minimax(prompt: str) -> str:
    """Call MiniMax with retry+backoff. Returns response content or empty string.

    2026-04-21: migrated from raw requests.post to post_with_retry. Previously
    a single 120s attempt with no retry -- any network hiccup meant zero
    directives for the whole 45-minute poll cycle. post_with_retry handles 5xx,
    ConnectionError, and Timeout with exponential backoff.
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        return ""
    r = post_with_retry(
        "https://api.minimax.io/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        # 2026-04-21: reasoning_split=True separates the model's chain-of-thought
        # into a dedicated reasoning_details field instead of embedding it in
        # content via <think>...</think> tags. Removes 40-70% of bytes per
        # response and removes the unclosed-tag parse failure mode.
        # Confirmed against https://platform.minimax.io/docs/api-reference/text-openai-api
        # The stripper in generate_directives() is kept as belt-and-suspenders
        # in case MiniMax silently changes/removes the parameter again.
        json={"model": "MiniMax-M2.7",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 4096,
              "reasoning_split": True},
        retries=MINIMAX_RETRIES,
        backoff=MINIMAX_BACKOFF,
        timeout=MINIMAX_TIMEOUT
    )
    if r is None:
        log.warning("MiniMax call failed: all %d retries exhausted",
                    MINIMAX_RETRIES)
        return ""
    if r.status_code != 200:
        log.warning("MiniMax HTTP %d: %s", r.status_code, r.text[:200])
        return ""
    try:
        choices = r.json().get("choices", [])
        if not choices:
            log.warning("MiniMax returned 200 with no choices")
            return ""
        msg = choices[0].get("message", {})
        content = msg.get("content", "").strip()
        # Telemetry: log whether reasoning_split was honored
        rd = msg.get("reasoning_details")
        if rd:
            try:
                rd_chars = sum(len(x.get("text", "")) for x in rd if isinstance(x, dict))
                log.info("MiniMax reasoning_split honored: content=%db reasoning=%db",
                         len(content), rd_chars)
            except Exception:
                log.info("MiniMax reasoning_split honored (reasoning_details present)")
        else:
            log.info("MiniMax reasoning_split NOT honored: content=%db (stripper will run)",
                     len(content))
        return content
    except Exception as e:
        log.warning("MiniMax response parse error: %s", e)
        return ""


def call_ollama(prompt: str) -> str:
    """Call Ollama with retry+backoff. Returns response content or empty string.

    2026-04-21: migrated from raw requests.post to post_with_retry. Same
    motivation as call_minimax -- transient local hiccups shouldn't wipe
    out a whole poll cycle.
    """
    r = post_with_retry(
        OLLAMA_URL,
        json={
            "model": "llama3.2:3b",
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": 8192}
        },
        retries=OLLAMA_RETRIES,
        backoff=OLLAMA_BACKOFF,
        timeout=OLLAMA_TIMEOUT
    )
    if r is None:
        log.warning("Ollama call failed: all %d retries exhausted",
                    OLLAMA_RETRIES)
        return ""
    if r.status_code != 200:
        log.warning("Ollama HTTP %d: %s", r.status_code, r.text[:200])
        return ""
    try:
        return r.json().get("response", "").strip()
    except Exception as e:
        log.warning("Ollama response parse error: %s", e)
        return ""


# Regex patterns for stripping reasoning-mode preambles. Compiled once at
# module load. Added 2026-04-21 after MiniMax-M2.7 began returning
# <think>...</think> blocks by default mid-April 2026, breaking the
# existing parse strategies which assumed clean JSON / object / fenced /
# bracket-recoverable shapes but not reasoning-wrapped output.
# Reasoning sanitizer (JSON-target variant) consolidated into minimax_utils.py.
# Imported under the existing name so generate_directives' call site is unchanged.
from minimax_utils import strip_reasoning_json as _strip_reasoning_preamble  # noqa: E402


def generate_directives(prompt: str) -> list:
    """Call LLM, parse list of directives from response.

    Tolerant parser. Strips reasoning-mode preambles first (MiniMax M2+
    emits <think>...</think> by default as of mid-April 2026), then tries
    these strategies in order:
      1. Bare JSON array: `[{...}, {...}]`
      2. Object-wrapped array: `{"directives": [...]}` -- MiniMax sometimes does this
      3. Markdown-fenced: ```json ... ``` or ``` ... ```
      4. Mixed content: strip preamble/postamble around first complete JSON value

    Never raises. Returns empty list on total failure.
    """
    raw = call_minimax(prompt)
    if not raw:
        log.info("MiniMax unavailable, trying Ollama")
        raw = call_ollama(prompt)
    if not raw:
        log.warning("No LLM response")
        return []

    text = raw.strip()

    # Strategy 0: strip reasoning-mode preambles (MiniMax M2+, similar models)
    text, stripped = _strip_reasoning_preamble(text)
    if stripped:
        log.info("Stripped reasoning-mode preamble from LLM response")

    # Strategy 1: strip markdown fences if present
    if text.startswith("```"):
        # Drop first line (``` or ```json), keep until closing ```
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

    # Strategy 2: try bare parse (may already be clean)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # Object-wrapped: {"directives": [...]} or {"result": [...]} or similar
            for key in ("directives", "result", "items", "data", "list"):
                if key in parsed and isinstance(parsed[key], list):
                    log.info("JSON was object-wrapped under key %r", key)
                    return parsed[key]
            # Single dict: wrap as one-item list
            log.info("JSON was a single dict; wrapping as one-element list")
            return [parsed]
    except json.JSONDecodeError:
        pass

    # Strategy 3: find first [ ... matching-depth ]
    start = text.find("[")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, list):
                            log.info("JSON recovered via bracket matching (%d-%d)",
                                     start, i+1)
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break

    # Strategy 4: find first { ... matching-depth } and check for wrapped list
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            for key in ("directives", "result", "items", "data", "list"):
                                if key in parsed and isinstance(parsed[key], list):
                                    log.info("JSON recovered via object wrapping "
                                             "under key %r", key)
                                    return parsed[key]
                    except json.JSONDecodeError:
                        pass
                    break

    log.warning("JSON parse failed across all strategies. First 200 chars: %r",
                text[:200])
    return []


REQUIRED_FIELDS = {"task", "handler", "output_file", "description"}
VALID_HANDLERS  = {"generate_file", "write_raw", "run_script"}
VALID_COMPLEXITY = {"low", "medium", "high"}

# Files that already exist and must not be re-queued

# Files that are WORKING and hand-calibrated. Validator rejects any directive
# targeting these. Removal guidance: only remove an entry when the module is
# superseded OR an explicit rebuild directive has been human-approved.
#
# UI entries: kept because they've been served via Zo preview and user-tested.
# As the UI is redesigned, revisit these entries.
PROTECTED_FILES = {
    'advanced_filter_api.py',
    'approval_workflow.py',
    'attestation_engine.py',
    'bulk_assess_api.py',
    'comparison_api.py',
    'dashboard.html',
    'dashboard_api.py',
    'forensic_detail_api.py',
    'full_schema_bootstrap.py',
    'inference_router_service.py',
    'manual_override_api.py',
    'mcp_scanner.py',
    'registry_api.py',
    'rug_pull_monitor.py',
    'search_api.py',
    'sentinel_status.html',
    'signal_analyser.py',
    'threat_intel_ingestor.py',
    'trust_synthesiser.py',
    'ui_server.py',
    'write_service.py',
}

ALREADY_BUILT = {"advanced_filter_api.py", "alert_manager.py", "analyst_feedback_loop.py", "anomaly_detector.py", "api_gateway.py", "api_health_checker.py", "approval_anomaly_detector.py", "approval_workflow.py", "assessment_scheduler.py", "attestation_engine.py", "audit_trail.py", "backup_service.py", "behavioral_analyser.py", "build_watcher_api.py", "bulk_assess_api.py", "certificate_analyser.py", "comparison_api.py", "compliance_export_service.py", "compliance_reporter.py", "config_validator.py", "context_injector.py", "context_manipulation_detector.py", "cross_registry_correlator.py", "cve_enricher.py", "daily_digest.py", "dashboard.html", "dashboard_api.py", "data_validator.py", "db_utils.py", "deduplicator.py", "dependency_chain_auditor.py", "directive_validator.py", "email_guid_auth.py", "error_reporter.py", "exemption_manager.py", "false_positive_tracker.py", "forensic_detail_api.py", "github_pr_checker.py", "github_repo_velocity.py", "http_retry.py", "integration_test.py", "known_threats.py", "lookup.py", "manifest_blast_radius.py", "manual_override_api.py", "mcp_age_risk_scorer.py", "mcp_data_seeder.py", "mcp_fingerprinter.py", "mcp_impersonation_detector.py", "mcp_profiler.py", "mcp_scanner.py", "mesh_bridge.py", "metrics_exporter.py", "notification_hub.py", "npm_typo_squatter.py", "npm_webhook_handler.py", "pattern_learner.py", "performance_monitor.py", "pipeline_health.py", "policy_engine.py", "prompt_injection_scanner.py", "queue_manager.py", "quick_seed.py", "rate_limiter.py", "registry_api.py", "registry_reconciler.py", "remediation_advisor.py", "report_formatter.py", "risk_ranker.py", "rug_pull_monitor.py", "run_schema.py", "runtime_behaviour_profiler.py", "schema.py", "schema_v2.py", "scoring_cache.py", "search_api.py", "sentinel_cli.py", "sentinel_sdk.py", "sentinel_status.html", "shodan_exposure_correlator.py", "signal_analyser.py", "similarity_scorer.py", "smoke_evolution_agent.py", "stale_data_cleaner.py", "sybil_burst_detector.py", "text_patterns.py", "threat_correlator.py", "threat_feed_aggregator.py", "threat_intel_ingestor.py", "tool_schema_deep_scanner.py", "trend_analyser.py", "trust_score_time_series.py", "trust_synthesiser.py", "ui_server.py", "url_analyser.py", "vendor_concentration_monitor.py", "verdict_explainer.py", "watch.py", "webhook_dispatcher.py"}

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
    if output in PROTECTED_FILES:
        return False, f"protected (hand-calibrated, do not regenerate): {output}"
    # Commit 2: enforce quarantine / retry-budget at validation layer
    # so even a non-compliant LLM can't bypass the breaker
    try:
        import gate_quality_state as _gqs
        _ok, _reason = _gqs.may_rebuild(output)
        if not _ok:
            return False, f"quality gate blocks rebuild of {output}: {_reason}"
    except Exception:
        pass  # fail-open on breaker infra error; prompt guidance still applies
    if len(d.get("description", "")) < 50:
        return False, "description too short (<50 chars)"
    return True, "ok"

def already_queued(task: str) -> bool:
    """Check if a PENDING directive matches this task name.

    Pending = .json file that is NOT a .done.json or .failed.json. Done/failed
    files are completed work and must NOT block re-emission of new tasks whose
    names happen to share a 30-char prefix.

    Bug history: filed 2026-04-28 (post-CTO-audit). Fixed 2026-05-02 v1.2 after
    an 80-minute builder starvation (10:44->12:04 UTC on 2026-05-02) caused
    by every LLM suggestion matching a historical .done.json by 30-char prefix.
    Robin's standing rule: directives must NEVER be empty. This patch closes
    the systemic cause.
    """
    pattern = f"*{task[:30]}*.json"
    for p in DIRECTIVE_DIR.glob(pattern):
        name = p.name
        if name.endswith(".done.json"):
            continue
        if name.endswith(".failed.json"):
            continue
        return True
    return False

def write_directive(d: dict, index: int) -> bool:
    task = d.get("task", f"gen_{index}")
    key  = hashlib.md5(task.encode()).hexdigest()[:8]
    path = DIRECTIVE_DIR / "pending" / f"gen_{key}_{task[:35]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(d, indent=2))
        log.info("  Written directive: %s -> %s", task, d.get("output_file", "?"))
        return True
    except Exception as e:
        log.error("  Write failed: %s", e)
        return False


# ── Standing Goals Fallback (2026-04-27) ─────────────────────────────

def _fallback_to_standing_goals(max_n: int = FALLBACK_MAX) -> int:
    """Emit standing-goal directives when LLM suggestions fully deduped.

    Returns the number of directives actually written. Catches all exceptions
    so a fallback failure never breaks the main cycle.
    """
    if _emit_standing_goals is None:
        return 0
    written = 0
    try:
        candidates = _emit_standing_goals(DIRECTIVE_DIR, max_n=max_n)
        for i, d in enumerate(candidates):
            valid, reason = validate_directive(d)
            if not valid:
                log.warning("  fallback skip [%d]: %s", i, reason)
                continue
            if already_queued(d["task"]):
                log.info("  fallback already queued: %s", d["task"])
                continue
            if write_directive(d, 1000 + i):
                written += 1
    except Exception as e:
        log.error("Standing goals fallback failed: %s", e)
    return written


# ── Main Cycle ───────────────────────────────────────────

def run_cycle():
    # Refresh DB schema doc so directives always reflect live DB (added 2026-04-16)
    try:
        import subprocess
        subprocess.run(
            ["python3", "/home/workspace/zo_sentinel/refresh_schema_doc.py"],
            timeout=20, capture_output=True, check=False
        )
    except Exception as e:
        log.warning("schema refresh failed: %s", e)
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

    # Layer 1: pull live knowledge sources (spec, wiring, gaps)
    try:
        layer1 = dks.assemble_layer1_context()
    except Exception as e:
        log.warning("Layer 1 context assembly failed: %s", e)
        layer1 = {"product_spec": "[Layer 1 unavailable]",
                  "wiring_map": "[Layer 1 unavailable]",
                  "gaps_map": "[Layer 1 unavailable]",
                  "quality_map": "[Layer 1 unavailable]"}
    prompt = build_prompt(schema, failed, failures_detail, reg_summary, queue,
                          layer1=layer1)
    log.info("Prompt: %d chars", len(prompt))

    directives = generate_directives(prompt)
    written = 0
    if not directives:
        log.warning("LLM returned no directives")
    else:
        log.info("LLM suggested %d directives", len(directives))
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
        log.info("Generation complete: %d/%d directives written",
                 written, len(directives))

    # 2026-04-27: fallback to standing goals when LLM yielded zero new directives.
    # Robin's hard rule: builder queue must NEVER be empty.
    if written == 0:
        log.warning(
            "Zero directives written from LLM cycle -- engaging standing_goals fallback"
        )
        fb = _fallback_to_standing_goals(max_n=FALLBACK_MAX)
        log.info("Standing goals fallback: %d directives written", fb)
        written += fb

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
    _start_heartbeat_thread()
    log.info("=" * 60)
    log.info("ZO-SENTINEL Directive Generator v1.2 (already_queued done-file fix)")
    log.info("  Schema: %s", SCHEMA_PATH)
    log.info("  Poll:   every %ds", POLL_SECS)
    log.info("  Skip if queue >= %d directives", MIN_QUEUE_TO_SKIP)
    log.info("  MiniMax: %s (timeout=%ds, retries=%d)",
             "SET" if os.environ.get("MINIMAX_API_KEY") else "NOT SET (Ollama fallback)",
             MINIMAX_TIMEOUT, MINIMAX_RETRIES)
    log.info("  Ollama:  timeout=%ds, retries=%d", OLLAMA_TIMEOUT, OLLAMA_RETRIES)
    log.info("  Standing goals fallback: %s (max %d per cycle)",
             "ENABLED" if _emit_standing_goals else "DISABLED (import failed)",
             FALLBACK_MAX)
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