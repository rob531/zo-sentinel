#!/usr/bin/env python3
"""
signal_labeler_filebridge.py v1.1  (2026-04-30)

v1.1 changes
------------
- Pre-emit in-flight scan: before pulling unlabeled MCPs from the DB, scan
  shared/work/probes/ for pending signal_label_*.json specs AND
  shared/outputs/signal_label/ for pending result files. Exclude any
  server_id we find from the unlabeled-MCPs query.
  Closes the race where two consecutive dispatcher invocations (lock
  released between them) would emit duplicate specs covering the same MCPs
  before the consumer had a chance to update the DB. Tower compute is
  finite; double-running 50 MCPs through Ollama on tower CPU costs ~10
  minutes of wasted wall clock per chunk.
- Stale spec / result TTL: if a spec or result file is older than
  IN_FLIGHT_TTL_HOURS (default 24h), do NOT treat its server_ids as in
  flight. Anything that old is wedged and the consumer / tower will need a
  separate fix; better to keep the unlabeled queue moving.
- Verdict output now includes 'in_flight_excluded' and 'in_flight_diag'
  so we can monitor how often the dedupe is firing.

v1.0 unchanged below.

File-bridge sibling of signal_labeler.py / signal_labeler_sonnet.py.

Instead of submitting an Anthropic Batches API request, this writes a
batch spec into the Syncthing-shared work tree at
  /home/workspace/shared/work/probes/signal_label_<batch_id>.json

ZoWarmWorker on the tower picks the spec up within 60s, dispatches via
Invoke-Probe.ps1 -> Invoke-SignalLabel.ps1, calls the tower's local
Ollama (with our SFT student model), writes a result file to
  /home/workspace/shared/outputs/signal_label/<batch_id>_<ts>_result.json

signal_label_consumer.py (separate daemon) ingests the results into
signal_training_corpus with teacher_model=<student tag>.

This is fire-and-forget. Idempotent on re-run via:
  (a) the lock file (TTL 1h)
  (b) the in-flight scan (this version's addition)
  (c) the DB NOT EXISTS join (always)

USAGE
  Direct:
    STUDENT_MODEL=qwen2.5-3b-sentinel-v1 python3 signal_labeler_filebridge.py
  Smoke (5 MCPs only):
    python3 signal_labeler_filebridge.py --smoke
  Custom chunk size / cap:
    python3 signal_labeler_filebridge.py --chunk-size 25 --max-mcps 200

ENV
  STUDENT_MODEL          (required) ollama tag the tower should call
  STUDENT_RUN_ID         (optional) override the auto-generated run id
  CHUNK_SIZE             (optional, default 50) MCPs per spec file
  MAX_MCPS_PER_INVOKE    (optional, default 1000) cap per script run
  IN_FLIGHT_TTL_HOURS    (optional, default 24) age past which pending
                         specs/results no longer block re-emission
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.1"
WRITE_SERVICE = "http://127.0.0.1:8772"

# Where to drop spec files. ZoWarmWorker on the tower watches this
# directory (via Syncthing). Existing whitelist already includes 'probes'
# as a directory; we discriminate via probe_type.
WORK_DIR = Path("/home/workspace/shared/work/probes")

# Where the tower writes result files (BOM-less UTF-8). signal_label_consumer
# polls this directory at 5s intervals; once it ingests a file it moves
# the file to <DIR>/processed/.  Anything in the top-level dir is pending.
RESULT_DIR = Path("/home/workspace/shared/outputs/signal_label")

OUT_DIR        = Path("/home/workspace/shared/outputs/probes")
STATE_DIR      = Path("/home/workspace/shared/outputs/signal_training_corpus")
LOCK_PATH      = Path("/home/workspace/logs/_signal_labeler_filebridge.lock")
STATE_PATH     = STATE_DIR / ".batch_state_filebridge.json"
LOCK_TTL_SEC   = 3_600

PAGE_SIZE = 200
MAX_PAGES = 500

DEFAULT_CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "50"))
DEFAULT_MAX_MCPS   = int(os.environ.get("MAX_MCPS_PER_INVOKE", "1000"))
PER_ITEM_TIMEOUT_SEC = int(os.environ.get("PER_ITEM_TIMEOUT_SEC", "120"))
IN_FLIGHT_TTL_HOURS  = float(os.environ.get("IN_FLIGHT_TTL_HOURS", "24"))

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
SIGNAL_NAMES = [s["name"] for s in SIGNALS]

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


# ── lock + verdict ─────────────────────────────────────────────────────────

def acquire_lock() -> bool:
    try:
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < LOCK_TTL_SEC:
                return False
        LOCK_PATH.write_text(
            f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n"
        )
        return True
    except Exception:
        return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except Exception:
        pass


def write_verdict(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"labeler_filebridge_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# ── in-flight scan (NEW in v1.1) ───────────────────────────────────────────────────

def _read_json_tolerant(p: Path) -> dict | None:
    """BOM-tolerant JSON read; return None on any failure rather than raise."""
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _file_age_hours(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 3600.0
    except Exception:
        return 0.0


def scan_in_flight_server_ids(ttl_hours: float) -> tuple[set[str], dict]:
    """Return server_ids that currently have a pending spec or pending
    result file, so the dispatcher doesn't re-emit them.

    Pending = file in the top-level directory (not in processed/ or failed/
    subdirs that the dispatcher / consumer move handled work into) AND
    file mtime within ttl_hours of now.

    Files older than ttl_hours are treated as wedged — the tower or consumer
    is presumably broken; rather than block re-labeling forever, we let the
    DB NOT EXISTS join + UNIQUE constraint sort it out at write time.
    """
    in_flight: set[str] = set()
    diag = {
        "work_dir":          str(WORK_DIR),
        "result_dir":        str(RESULT_DIR),
        "ttl_hours":         ttl_hours,
        "pending_specs":     0,
        "pending_results":   0,
        "stale_specs_skipped":   0,
        "stale_results_skipped": 0,
        "unparseable_specs":     0,
        "unparseable_results":   0,
    }

    # Pending specs: shared/work/probes/signal_label_*.json (top-level only)
    if WORK_DIR.exists():
        for p in WORK_DIR.iterdir():
            if not p.is_file():
                continue
            if not p.name.startswith("signal_label_") or not p.name.endswith(".json"):
                continue
            if _file_age_hours(p) > ttl_hours:
                diag["stale_specs_skipped"] += 1
                continue
            spec = _read_json_tolerant(p)
            if spec is None:
                diag["unparseable_specs"] += 1
                continue
            for item in spec.get("items", []) or []:
                sid = item.get("server_id") if isinstance(item, dict) else None
                if sid:
                    in_flight.add(sid)
            diag["pending_specs"] += 1

    # Pending results: shared/outputs/signal_label/*.json (top-level only).
    # signal_label_consumer moves to processed/ once ingested.
    if RESULT_DIR.exists():
        for p in RESULT_DIR.iterdir():
            if not p.is_file() or not p.name.endswith(".json"):
                continue
            if _file_age_hours(p) > ttl_hours:
                diag["stale_results_skipped"] += 1
                continue
            payload = _read_json_tolerant(p)
            if payload is None:
                diag["unparseable_results"] += 1
                continue
            for r in payload.get("results", []) or []:
                sid = r.get("server_id") if isinstance(r, dict) else None
                if sid:
                    in_flight.add(sid)
            diag["pending_results"] += 1

    diag["unique_server_ids_in_flight"] = len(in_flight)
    return in_flight, diag


# ── DB I/O ────────────────────────────────────────────────────────────────────────────

def ws_query(sql: str, timeout: int = 60) -> list | None:
    import requests
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=timeout,
        )
        if r.status_code == 200:
            return r.json().get("rows", [])
        return None
    except Exception:
        return None


def fetch_unlabeled_for_student(
    student_model: str,
    max_mcps: int,
    exclude: set[str] | None = None,
) -> list[dict]:
    """Pull MCPs not yet labeled by THIS student model, optionally filtering
    out an `exclude` set of server_ids that are currently in flight.

    Pagination keeps going until we have `max_mcps` post-filter rows or run
    out of pages. Filtering happens in Python (not SQL) because exclude can
    contain hundreds of IDs and inlining them in a NOT IN clause is brittle.
    """
    exclude = exclude or set()
    base_where = (
        "WHERE r.description IS NOT NULL "
        "AND length(r.description) > 30 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM signal_training_corpus c "
        "  WHERE c.server_id = r.server_id "
        f"  AND c.teacher_model = '{student_model}'"
        ")"
    )
    base_sql = (
        "SELECT r.server_id, r.name, r.description, r.registry_source, r.url "
        "FROM mcp_server_registry r " + base_where + " ORDER BY r.server_id"
    )
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        if len(rows) >= max_mcps:
            break
        offset = page * PAGE_SIZE
        page_sql = f"{base_sql} LIMIT {PAGE_SIZE} OFFSET {offset}"
        page_rows = ws_query(page_sql)
        if page_rows is None:
            break  # transport error, accept what we have
        if not page_rows:
            break
        for r in page_rows:
            if r.get("server_id") in exclude:
                continue
            rows.append(r)
            if len(rows) >= max_mcps:
                break
        if len(page_rows) < PAGE_SIZE:
            break
    return rows[:max_mcps]


# ── prompt ──────────────────────────────────────────────────────────────────────────────

def build_user_prompt(mcp: dict) -> str:
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


# ── spec emission ──────────────────────────────────────────────────────────────────

def chunk_iter(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def new_batch_id(student_model: str, run_id: str, idx: int) -> str:
    safe_model = "".join(c if c.isalnum() else "_" for c in student_model)[:32]
    return f"signal_label_{safe_model}_{run_id}_chunk{idx:04d}"


def emit_spec(
    *,
    work_dir: Path,
    batch_id: str,
    student_model: str,
    teacher_run_id: str,
    chunk: list[dict],
) -> Path:
    items = []
    for mcp in chunk:
        items.append({
            "server_id":   mcp["server_id"],
            "mcp_name":    mcp.get("name", ""),
            "user_prompt": build_user_prompt(mcp),
        })
    spec = {
        "probe_type":           "signal_label",
        "handler":              "Invoke-SignalLabel.ps1",
        "batch_id":             batch_id,
        "emitted_at":           datetime.now(timezone.utc).isoformat(),
        "emitted_by":           f"signal_labeler_filebridge.py v{VERSION}",
        "emitted_host":         socket.gethostname(),
        "teacher_run_id":       teacher_run_id,
        "student_model_tag":    student_model,
        "model":                student_model,
        "system_prompt":        SYSTEM_PROMPT,
        "max_tokens":           1500,
        "temperature":          0.2,
        "per_item_timeout_sec": PER_ITEM_TIMEOUT_SEC,
        "items":                items,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_path = work_dir / f"{batch_id}.json"
    tmp = spec_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
    tmp.rename(spec_path)
    return spec_path


# ── main ─────────────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drop signal_label batch specs onto the tower file bridge")
    p.add_argument("--smoke",       action="store_true",
                   help="Smoke mode: emit a single 5-MCP spec and exit")
    p.add_argument("--chunk-size",  type=int, default=DEFAULT_CHUNK_SIZE)
    p.add_argument("--max-mcps",    type=int, default=DEFAULT_MAX_MCPS)
    p.add_argument("--student-model", default=os.environ.get("STUDENT_MODEL", ""))
    p.add_argument("--run-id",      default=os.environ.get("STUDENT_RUN_ID", ""))
    p.add_argument("--ttl-hours",   type=float, default=IN_FLIGHT_TTL_HOURS,
                   help="Age past which pending specs/results are treated as wedged.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    started_iso = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not args.student_model:
        write_verdict({
            "probe":      "signal_labeler_filebridge",
            "version":    VERSION,
            "started_at": started_iso,
            "verdict":    "missing_student_model",
            "reason":     "STUDENT_MODEL env var or --student-model arg required.",
            "hostname":   socket.gethostname(),
        })
        return 0

    if not acquire_lock():
        write_verdict({
            "probe":      "signal_labeler_filebridge",
            "version":    VERSION,
            "started_at": started_iso,
            "verdict":    "skipped_locked",
            "hostname":   socket.gethostname(),
        })
        return 0

    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        run_id     = args.run_id or f"student_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        chunk_size = max(1, min(args.chunk_size, 200))
        max_mcps   = max(1, args.max_mcps)
        ttl_hours  = max(0.5, args.ttl_hours)

        # NEW v1.1: in-flight scan before DB query
        in_flight, in_flight_diag = scan_in_flight_server_ids(ttl_hours)
        print(
            f"in-flight scan: "
            f"specs={in_flight_diag['pending_specs']} "
            f"results={in_flight_diag['pending_results']} "
            f"unique_ids={in_flight_diag['unique_server_ids_in_flight']} "
            f"stale_specs={in_flight_diag['stale_specs_skipped']} "
            f"stale_results={in_flight_diag['stale_results_skipped']}"
        )

        if args.smoke:
            mcps = fetch_unlabeled_for_student(
                args.student_model, 5, exclude=in_flight
            )
            chunk_size = 5
        else:
            mcps = fetch_unlabeled_for_student(
                args.student_model, max_mcps, exclude=in_flight
            )

        if not mcps:
            write_verdict({
                "probe":         "signal_labeler_filebridge",
                "version":       VERSION,
                "started_at":    started_iso,
                "finished_at":   datetime.now(timezone.utc).isoformat(),
                "duration_ms":   int((time.time() - t0) * 1000),
                "verdict":       "nothing_to_label",
                "reason": (
                    f"No MCPs unlabeled by student {args.student_model} "
                    f"(after excluding {len(in_flight)} in-flight server_ids)."
                ),
                "student_model":     args.student_model,
                "in_flight_excluded": len(in_flight),
                "in_flight_diag":     in_flight_diag,
                "hostname":          socket.gethostname(),
            })
            return 0

        emitted: list[dict] = []
        for i, chunk in enumerate(chunk_iter(mcps, chunk_size)):
            batch_id = new_batch_id(args.student_model, run_id, i)
            spec_path = emit_spec(
                work_dir=WORK_DIR,
                batch_id=batch_id,
                student_model=args.student_model,
                teacher_run_id=run_id,
                chunk=chunk,
            )
            emitted.append({
                "batch_id":  batch_id,
                "spec_path": str(spec_path),
                "mcp_count": len(chunk),
            })

        save_state({
            "last_run_id":      run_id,
            "student_model":    args.student_model,
            "last_emitted_at":  datetime.now(timezone.utc).isoformat(),
            "chunks_emitted":   len(emitted),
            "mcps_dispatched":  len(mcps),
            "in_flight_excluded": len(in_flight),
        })

        out = write_verdict({
            "probe":              "signal_labeler_filebridge",
            "version":            VERSION,
            "started_at":         started_iso,
            "finished_at":        datetime.now(timezone.utc).isoformat(),
            "duration_ms":        int((time.time() - t0) * 1000),
            "verdict":            "ok",
            "student_model":      args.student_model,
            "run_id":             run_id,
            "chunks_emitted":     len(emitted),
            "mcps_dispatched":    len(mcps),
            "chunk_size":         chunk_size,
            "smoke":              args.smoke,
            "in_flight_excluded": len(in_flight),
            "in_flight_diag":     in_flight_diag,
            "emitted":            emitted[:10],
            "hostname":           socket.gethostname(),
        })
        print(
            f"signal_labeler_filebridge -> ok  emitted={len(emitted)} "
            f"mcps={len(mcps)} smoke={args.smoke} "
            f"in_flight_excluded={len(in_flight)} -> {out}"
        )
        return 0

    except Exception as e:
        import traceback
        write_verdict({
            "probe":       "signal_labeler_filebridge",
            "version":     VERSION,
            "started_at":  started_iso,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.time() - t0) * 1000),
            "verdict":     "probe_exception",
            "error":       f"{type(e).__name__}: {e}",
            "traceback":   traceback.format_exc()[:3000],
            "hostname":    socket.gethostname(),
        })
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())