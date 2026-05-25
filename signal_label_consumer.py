#!/usr/bin/env python3
"""
signal_label_consumer.py v1.0  (2026-04-30)

Daemon. Watches /home/workspace/shared/outputs/signal_label/ for result
JSON files written by the tower's Invoke-SignalLabel.ps1, parses each
MCP's response_text into the canonical 6-signal label schema, writes
rows into signal_training_corpus with teacher_model = student tag.

Mirror of probe_consumer.py:
  - 5s poll loop
  - file_hash dedup
  - UTF-8-BOM tolerant JSON read (PowerShell historically wrote BOMs)
  - move processed files to processed/
  - service_health heartbeat every 60 cycles (~5 min)
  - meant to be supervisord-managed for reboot survival

FOR SUPERVISORD
  Add to /etc/zo/supervisord-user.conf:
    [program:signal_label_consumer]
    command=/usr/bin/python3 /home/workspace/zo_sentinel/signal_label_consumer.py
    directory=/home/workspace
    autostart=true
    autorestart=true
    stdout_logfile=/home/workspace/logs/signal_label_consumer.log
    stdout_logfile_maxbytes=10MB
    stdout_logfile_backups=3
    redirect_stderr=true
    environment=PYTHONUNBUFFERED="1"
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.0"
INBOX     = Path("/home/workspace/shared/outputs/signal_label")
PROCESSED = INBOX / "processed"
FAILED    = INBOX / "failed"
WS        = "http://127.0.0.1:8772"
POLL_S    = 5
SERVICE   = "signal_label_consumer"

# Same signal vocab as the labelers. Used to:
#  (a) ensure we always write all 6 signal rows per MCP (filling UNKNOWN if absent)
#  (b) ignore unrecognized signal keys silently rather than DB-poisoning
SIGNAL_NAMES = [
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

# Bound the in-memory dedup set; if we somehow process > 100k files this
# will wrap. Better than unbounded growth in a long-running daemon.
MAX_SEEN = 100_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [signal_label_consumer] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("/home/workspace/logs/signal_label_consumer.log", mode="a"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(SERVICE)

_seen_hashes: set[str] = set()
_stats = {
    "files_processed":    0,
    "files_failed":       0,
    "signals_written":    0,
    "items_ok":           0,
    "items_parse_failed": 0,
    "items_handler_err":  0,
}


# ── write_service ─────────────────────────────────────────────────────────────────────────

def ws_post(path: str, payload: dict) -> tuple:
    req = urllib.request.Request(
        f"{WS}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8")
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return None, str(e)


def ws_write(table: str, rows) -> bool:
    code, body = ws_post("/write", {"table": table, "rows": rows, "wait": True})
    if code != 200:
        log.warning("write %s failed code=%s body=%s", table, code, str(body)[:200])
        return False
    return True


def heartbeat() -> None:
    ws_write("service_health", {
        "service":        SERVICE,
        "status":         "running",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "meta":           json.dumps({
            "inbox":   str(INBOX),
            "version": VERSION,
            **_stats,
        }),
    })


# ── parsing ────────────────────────────────────────────────────────────────────────────────

def file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def read_json_tolerant(p: Path) -> dict:
    """BOM-tolerant JSON read — same belt+braces as probe_consumer."""
    return json.loads(p.read_text(encoding="utf-8-sig"))


def parse_response_text(text: str) -> tuple:
    """Same parser as signal_labeler.py. Returns (parsed_dict_or_None, err_msg)."""
    if not text:
        return None, "empty response"
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        end = len(lines) - 1
        while end > 0 and lines[end].strip() in ("", "```"):
            end -= 1
        cleaned = "\n".join(lines[1:end + 1]).strip()
    start = cleaned.find("{")
    last = cleaned.rfind("}")
    if start < 0 or last < 0 or last < start:
        return None, "no JSON braces"
    try:
        parsed = json.loads(cleaned[start:last + 1])
    except json.JSONDecodeError as e:
        return None, f"JSONDecodeError: {e}"
    if not isinstance(parsed, dict) or "signals" not in parsed:
        return None, "missing signals key"
    return parsed, ""


# ── ingest ───────────────────────────────────────────────────────────────────────────────

def ingest_result_file(payload: dict, source_file: str) -> dict:
    """Ingest one result JSON file. Returns per-file counts."""
    counts = {"items_ok": 0, "items_parse_failed": 0, "items_handler_err": 0,
              "signals_written": 0}

    if payload.get("status") == "error":
        log.warning("handler error file %s: %s", source_file, payload.get("error"))
        return counts

    if payload.get("probe_type") != "signal_label":
        log.warning("unexpected probe_type=%r in %s",
                    payload.get("probe_type"), source_file)
        return counts

    student_model = (
        payload.get("model")
        or payload.get("student_model_tag")
        or "unknown_student"
    )
    teacher_run_id = (
        payload.get("teacher_run_id")
        or payload.get("batch_id", "unknown_run")
    )

    rows_to_write: list[dict] = []
    for r in payload.get("results", []) or []:
        server_id = r.get("server_id", "")
        if not server_id:
            continue

        if not r.get("ok"):
            counts["items_handler_err"] += 1
            continue

        text = r.get("response_text", "")
        parsed, perr = parse_response_text(text)
        if not parsed:
            counts["items_parse_failed"] += 1
            log.info("parse failure server_id=%s err=%s preview=%r",
                     server_id, perr, (text or "")[:120])
            continue

        counts["items_ok"] += 1
        thought      = parsed.get("thought_process", "")
        overall_risk = parsed.get("overall_risk", "")
        signals_obj  = parsed.get("signals", {}) or {}

        for sig_name in SIGNAL_NAMES:
            sig_payload = signals_obj.get(sig_name, {}) or {}
            value    = sig_payload.get("value", "UNKNOWN")
            evidence = sig_payload.get("evidence", "")
            rows_to_write.append({
                "server_id":       server_id,
                "mcp_name":        "",   # not echoed back from tower; cheap to leave blank, lookup-able via registry
                "signal_name":     sig_name,
                "signal_value":    value,
                "signal_evidence": evidence,
                "thought_process": thought,
                "overall_risk":    overall_risk,
                "teacher_model":   student_model,
                "teacher_run_id":  teacher_run_id,
            })
            counts["signals_written"] += 1

    if rows_to_write:
        # Chunked writes (200/req) match probe_consumer batching
        for i in range(0, len(rows_to_write), 200):
            chunk = rows_to_write[i:i + 200]
            ws_write("signal_training_corpus", chunk)

    # Also emit a mesh_event so the rest of the mesh can see throughput
    ws_write("mesh_events", {
        "agent_id":   SERVICE,
        "event_type": "signal_label_batch_ingested",
        "tier":       "T2",
        "payload":    json.dumps({
            "batch_id":      payload.get("batch_id"),
            "student_model": student_model,
            "counts":        counts,
            "source_file":   source_file,
            "tower_host":    payload.get("tower_host"),
            "duration_s":    payload.get("duration_s"),
        }),
        "severity":   "INFO",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return counts


# ── file pump ───────────────────────────────────────────────────────────────────────────────

def move_to(p: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(p), str(dest_dir / p.name))
    except Exception as e:
        log.warning("could not move %s to %s: %s", p.name, dest_dir, e)


def process_one(p: Path) -> None:
    try:
        h = file_hash(p)
    except Exception as e:
        log.warning("could not hash %s: %s", p.name, e)
        return
    if h in _seen_hashes:
        return
    try:
        payload = read_json_tolerant(p)
    except json.JSONDecodeError as e:
        log.warning("bad JSON %s: %s", p.name, e)
        _stats["files_failed"] += 1
        move_to(p, FAILED)
        return
    except Exception as e:
        log.warning("unreadable %s: %s", p.name, e)
        _stats["files_failed"] += 1
        return  # leave it in place; transient FS error

    counts = ingest_result_file(payload, p.name)

    _stats["files_processed"]    += 1
    _stats["items_ok"]           += counts.get("items_ok", 0)
    _stats["items_parse_failed"] += counts.get("items_parse_failed", 0)
    _stats["items_handler_err"]  += counts.get("items_handler_err", 0)
    _stats["signals_written"]    += counts.get("signals_written", 0)

    _seen_hashes.add(h)
    if len(_seen_hashes) > MAX_SEEN:
        # Keep memory bounded; oldest are arbitrary since set, that's fine
        _seen_hashes.clear()
        _seen_hashes.add(h)

    move_to(p, PROCESSED)
    log.info(
        "ingested %s ok=%d parse_failed=%d handler_err=%d signals=%d",
        p.name, counts["items_ok"], counts["items_parse_failed"],
        counts["items_handler_err"], counts["signals_written"],
    )


def scan_once() -> None:
    if not INBOX.exists():
        INBOX.mkdir(parents=True, exist_ok=True)
        return
    for p in sorted(INBOX.iterdir()):
        if not p.is_file() or not p.name.endswith(".json"):
            continue
        try:
            process_one(p)
        except Exception as e:
            log.exception("process_one failed for %s: %s", p.name, e)


def main() -> int:
    log.info("=" * 60)
    log.info("signal_label_consumer v%s starting (poll every %ds)", VERSION, POLL_S)
    log.info("  inbox: %s", INBOX)
    log.info("  ws:    %s", WS)
    log.info("=" * 60)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    FAILED.mkdir(parents=True, exist_ok=True)
    cycle = 0
    heartbeat()
    while True:
        try:
            scan_once()
            cycle += 1
            if cycle % 60 == 0:
                heartbeat()
        except KeyboardInterrupt:
            log.info("interrupt")
            break
        except Exception as e:
            log.exception("scan loop error: %s", e)
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())