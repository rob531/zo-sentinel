#!/usr/bin/env python3
"""
signal_labeler_student.py  v1.0  (2026-05-01)

Third teacher tier in the Phase A / Phase C signal corpus pipeline.

This is the FILE-BRIDGE counterpart to signal_labeler.py (Haiku) and
signal_labeler_sonnet.py (Sonnet 4.5).  Where those submit batches to
Anthropic via API, this script writes batch specs to the canonical Syncthing
tree for the tower's local Ollama-served SFT student to consume.

Flow (bridge 6 + bridge 7 in /home/workspace/shared/BRIDGES.md):

  1. signal_labeler_student.py  (this script)
     -> writes /home/workspace/shared/work/signal_label/<batch>.json
     -> Syncthing carries to tower's C:\\Users\\robin\\ZoComputer\\shared\\work\\signal_label\\
     -> tower scheduled task ZoSignalLabelWorker fires every 60s
     -> dispatches each batch to Invoke-SignalLabel.ps1
     -> Invoke-SignalLabel.ps1 calls tower's local Ollama
        with the SFT student model, writes result JSON
     -> Syncthing carries result back to ZO at /home/workspace/shared/outputs/signal_label/
     -> signal_label_consumer.py polls, parses, writes to signal_training_corpus

Idempotency: this dispatcher MAINTAINS its own state in
.batch_state_student.json so re-runs skip already-dispatched MCPs and
mid-flight batches.  The consumer is similarly idempotent.

Dispatcher does NOT wait for results -- the consumer is a separate daemon.
This keeps dispatch fast (one cron tick) regardless of tower throughput.

Must match signal_labeler.py / signal_labeler_sonnet.py exactly on:
  - SIGNALS list (definitions, value enums)
  - SYSTEM_PROMPT
  - build_user_prompt() shape
because the student is trained on the same prompt the teachers saw.
Deviation = student sees inputs it wasn't trained on = quality collapse.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "1.0"
WRITE_SERVICE = "http://127.0.0.1:8772"

# Where the tower-side worker picks up specs (Syncthing-mirrored).
WORK_DIR    = Path("/home/workspace/shared/work/signal_label")
STATE_PATH  = Path("/home/workspace/shared/outputs/signal_training_corpus/.batch_state_student.json")
LOCK_PATH   = Path("/home/workspace/logs/_signal_labeler_student.lock")
OUT_DIR     = Path("/home/workspace/shared/outputs/probes")
LOCK_TTL_SEC = 3_600

# Student configuration.  Override via env for experimentation without code edits.
STUDENT_MODEL  = os.environ.get("STUDENT_MODEL",  "qwen2.5-3b-sentinel-v1")
STUDENT_LABEL  = os.environ.get("STUDENT_LABEL",  STUDENT_MODEL)  # what we write to teacher_model column
OLLAMA_URL     = os.environ.get("TOWER_STUDENT_OLLAMA", "http://localhost:11434")  # tower-local URL
MAX_TOKENS     = int(os.environ.get("STUDENT_MAX_TOKENS", "1500"))
TEMPERATURE    = float(os.environ.get("STUDENT_TEMPERATURE", "0.2"))

# Batch sizing.  Larger batches = fewer round trips but longer worst-case latency.
BATCH_SIZE     = int(os.environ.get("STUDENT_BATCH_SIZE", "50"))
MCP_LIMIT      = None  # set to int for a partial run

# DB pagination -- write_service /query caps at 200 rows.
PAGE_SIZE      = 200
MAX_PAGES      = 500

# === SIGNALS / SYSTEM_PROMPT -- must match signal_labeler*.py exactly =========
SIGNALS = [
    {
        "name": "auth_strength",
        "definition": "How securely the MCP authenticates clients. Values: STRONG (mTLS, OAuth with PKCE, hardware tokens), MODERATE (OAuth with secrets, session tokens), WEAK (API keys, basic auth, no auth), UNKNOWN.",
        "values": ["STRONG", "MODERATE", "WEAK", "UNKNOWN"],
    },
    {
        "name": "capability_breadth",
        "definition": "How broad the tool's capabilities are. NARROW (one specific function), MODERATE (a focused domain), BROAD (general-purpose / many tools), UNKNOWN.",
        "values": ["NARROW", "MODERATE", "BROAD", "UNKNOWN"],
    },
    {
        "name": "data_sensitivity",
        "definition": "Sensitivity of data the MCP handles. PUBLIC (open data, web), INTERNAL (org data, low-risk), SENSITIVE (PII, secrets, financial), CRITICAL (auth tokens, credentials, prod data), UNKNOWN.",
        "values": ["PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"],
    },
    {
        "name": "network_egress",
        "definition": "Whether the MCP can reach external networks. NONE (local-only), INTERNAL (intranet only), EXTERNAL (internet), ARBITRARY (user-controlled URLs / SSRF risk), UNKNOWN.",
        "values": ["NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"],
    },
    {
        "name": "maintainer_trust",
        "definition": "Trustworthiness of the maintainer. ESTABLISHED (well-known org, long history), VERIFIED (signed packages, GitHub-verified org), COMMUNITY (active OSS project, multiple contributors), UNKNOWN_AUTHOR (unknown individual or empty signals), SUSPICIOUS (red flags: typosquat, sudden ownership change, malicious history).",
        "values": ["ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR", "SUSPICIOUS"],
    },
    {
        "name": "exploit_surface",
        "definition": "Estimated exploit surface for an attacker. MINIMAL (read-only, sandboxed), LIMITED (writes to scoped resources), MODERATE (multiple capabilities, some elevated), BROAD (file system, command execution, or arbitrary code paths), UNKNOWN.",
        "values": ["MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN"],
    },
]

SYSTEM_PROMPT = (
    "You are a security analyst labeling MCP (Model Context Protocol) servers "
    "for an enterprise risk register. You will receive an MCP description and "
    "must produce a JSON object with a score for each of 6 signals plus a "
    "chain-of-thought reasoning string.\n\n"
    "OUTPUT FORMAT: ONLY a JSON object. NO markdown fences. NO commentary. "
    "Start your response with '{' and end with '}'. The JSON must contain:\n"
    "  - 'thought_process': string -- 2-4 sentences of reasoning across signals\n"
    "  - 'signals': object with 6 keys (one per signal name), each value is an "
    "object {value, evidence}.\n"
    "  - 'overall_risk': string -- one of LOW, MEDIUM, HIGH, CRITICAL.\n\n"
    "If information is missing, use UNKNOWN values. Never refuse. Never ask for "
    "clarification. Output JSON only."
)
# ==============================================================================


def acquire_lock() -> bool:
    try:
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < LOCK_TTL_SEC:
                return False
        LOCK_PATH.write_text(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n")
        return True
    except Exception:
        return True


def release_lock() -> None:
    try: LOCK_PATH.unlink()
    except Exception: pass


def write_probe_output(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"labeler_student_dispatch_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


def load_state() -> dict:
    if STATE_PATH.exists():
        try: return json.loads(STATE_PATH.read_text())
        except Exception: pass
    return {"dispatched_batches": {}, "dispatched_server_ids": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def ws_query(sql: str, timeout: int = 60) -> list | None:
    import requests
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("rows", [])
        return None
    except Exception:
        return None


def ws_query_count(sql: str) -> int:
    rows = ws_query(sql)
    if not rows: return -1
    return int(rows[0].get("n", -1))


def ws_query_paginated(base_sql_no_limit: str, expected: int | None = None) -> list:
    """Paginate with retries on transient empty pages.  Same shape as the v1.2
    retry logic in signal_corpus_export.py."""
    rows: list = []
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        page_sql = f"{base_sql_no_limit} LIMIT {PAGE_SIZE} OFFSET {offset}"
        page_rows = None
        for attempt in range(5):
            page_rows = ws_query(page_sql)
            if page_rows is None:
                time.sleep(0.5 * (2 ** attempt))
                continue
            if len(page_rows) > 0:
                break
            if expected is not None and len(rows) >= expected:
                break
            time.sleep(0.5 * (2 ** attempt))
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < PAGE_SIZE and (expected is None or len(rows) >= expected):
            break
    return rows


def build_user_prompt(mcp: dict) -> str:
    """Reconstruct the EXACT user prompt the teachers saw.  Must match
    signal_labeler.py / signal_labeler_sonnet.py / signal_corpus_export.py."""
    signals_block = "\n".join(
        f"  {i+1}. {s['name']}: {s['definition']}"
        f"\n     Values: {' | '.join(s['values'])}"
        for i, s in enumerate(SIGNALS)
    )
    return (
        f"MCP SERVER UNDER REVIEW:\n"
        f"  server_id: {mcp.get('server_id', '?')}\n"
        f"  name:      {mcp.get('name', '?')}\n"
        f"  source:    {mcp.get('registry_source', '?')}\n"
        f"  url:       {mcp.get('url', '?')}\n"
        f"  description: {mcp.get('description', '(none)')}\n\n"
        f"SIGNALS TO LABEL:\n{signals_block}\n\n"
        f"Output a single JSON object now."
    )


def fetch_mcps_to_label(limit: int | None = None) -> list[dict]:
    """Pull MCPs that don't yet have student labels.  NOT EXISTS guards against
    re-labeling MCPs already in the corpus from a previous student dispatch."""
    base_sql = (
        "SELECT r.server_id, r.name, r.description, r.registry_source, r.url "
        "FROM mcp_server_registry r "
        "WHERE r.description IS NOT NULL "
        "  AND length(r.description) > 30 "
        "  AND NOT EXISTS ("
        "    SELECT 1 FROM signal_training_corpus c "
        f"   WHERE c.server_id = r.server_id AND c.teacher_model = '{STUDENT_LABEL}'"
        "  ) "
        "ORDER BY r.server_id"
    )
    rows = ws_query_paginated(base_sql)
    if limit is not None:
        rows = rows[:limit]
    return rows


def build_batch_spec(
    *,
    batch_id: str,
    teacher_run_id: str,
    mcps: list[dict],
) -> dict:
    items = []
    for mcp in mcps:
        items.append({
            "server_id":     mcp["server_id"],
            "mcp_name":      mcp.get("name", ""),
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt":   build_user_prompt(mcp),
        })
    return {
        "schema_version": "1.0",
        "batch_id":       batch_id,
        "teacher_run_id": teacher_run_id,
        "model":          STUDENT_MODEL,
        "teacher_label":  STUDENT_LABEL,
        "ollama_url":     OLLAMA_URL,
        "max_tokens":     MAX_TOKENS,
        "temperature":    TEMPERATURE,
        "items":          items,
        "submitted_at":   datetime.now(timezone.utc).isoformat(),
        "submitted_by":   "signal_labeler_student",
        "submitted_host": socket.gethostname(),
    }


def write_spec_atomically(spec: dict, batch_id: str) -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    final = WORK_DIR / f"signal_label_{batch_id}.json"
    tmp   = final.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
    tmp.replace(final)  # atomic on the same filesystem
    return final


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not acquire_lock():
        write_probe_output({
            "probe": "signal_labeler_student", "version": VERSION,
            "started_at": started, "verdict": "skipped_locked",
        })
        return 0

    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

        state = load_state()
        already_dispatched_ids = set(state.get("dispatched_server_ids", []))

        mcps = fetch_mcps_to_label(limit=MCP_LIMIT)
        # Belt-and-braces: also filter by dispatcher state in case the consumer
        # hasn't ingested back yet (specs in flight, results not yet in DB).
        mcps = [m for m in mcps if m["server_id"] not in already_dispatched_ids]

        if not mcps:
            write_probe_output({
                "probe": "signal_labeler_student", "version": VERSION,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "nothing_to_dispatch",
                "reason": f"all eligible MCPs already labeled or in flight under {STUDENT_LABEL}",
            })
            print(f"signal_labeler_student -> nothing to dispatch ({STUDENT_LABEL})")
            return 0

        teacher_run_id = f"run_student_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        dispatched_batches: list[dict] = []
        new_dispatched_ids: list[str] = []

        # Chunk into BATCH_SIZE-sized specs.
        for i in range(0, len(mcps), BATCH_SIZE):
            chunk = mcps[i:i + BATCH_SIZE]
            batch_seed = f"{teacher_run_id}-{i}".encode()
            batch_short = hashlib.blake2s(batch_seed, digest_size=4).hexdigest()
            batch_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{batch_short}"

            spec = build_batch_spec(
                batch_id=batch_id,
                teacher_run_id=teacher_run_id,
                mcps=chunk,
            )
            path = write_spec_atomically(spec, batch_id)
            dispatched_batches.append({
                "batch_id":    batch_id,
                "path":        str(path),
                "mcp_count":   len(chunk),
                "server_ids":  [m["server_id"] for m in chunk],
            })
            new_dispatched_ids.extend(m["server_id"] for m in chunk)
            print(f"  dispatched {batch_id}: {len(chunk)} MCPs -> {path.name}")

        # Update dispatcher state.
        state["dispatched_batches"][teacher_run_id] = {
            "started_at":   started,
            "teacher_run_id": teacher_run_id,
            "model":        STUDENT_MODEL,
            "label":        STUDENT_LABEL,
            "batch_count":  len(dispatched_batches),
            "mcp_count":    sum(b["mcp_count"] for b in dispatched_batches),
            "batches":      dispatched_batches,
        }
        state["dispatched_server_ids"] = sorted(
            list(already_dispatched_ids.union(new_dispatched_ids))
        )
        save_state(state)

        write_probe_output({
            "probe":         "signal_labeler_student",
            "version":       VERSION,
            "started_at":    started,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "duration_ms":   int((time.time() - t0) * 1000),
            "verdict":       "ok",
            "teacher_run_id": teacher_run_id,
            "model":         STUDENT_MODEL,
            "label":         STUDENT_LABEL,
            "batches_dispatched": len(dispatched_batches),
            "mcps_dispatched":    sum(b["mcp_count"] for b in dispatched_batches),
            "work_dir":      str(WORK_DIR),
            "hostname":      socket.gethostname(),
        })
        print(f"signal_labeler_student -> ok: {len(dispatched_batches)} batches, "
              f"{sum(b['mcp_count'] for b in dispatched_batches)} MCPs")
        print("  consumer (signal_label_consumer.py) will ingest results as they appear.")
        return 0

    except Exception as e:
        import traceback
        write_probe_output({
            "probe": "signal_labeler_student", "version": VERSION,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "probe_exception",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[:3000],
        })
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())