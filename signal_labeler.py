#!/usr/bin/env python3
"""
signal_labeler.py v1.1  (2026-04-30)

v1.1 changes
------------
- fetch_mcps_to_label() now PAGINATES through write_service /query, which
  caps responses at 200 rows. Iterates with LIMIT 200 OFFSET N until empty,
  accumulating the full eligible set. Adds ORDER BY server_id for stable
  pagination.
  Anthropic Batches API supports up to 10,000 requests per batch, so all
  4,118 currently-eligible MCPs fit in a single batch.

v1.0 unchanged
--------------
Labels every MCP in mcp_server_registry with 6 security signals via the
Anthropic Message Batches API on claude-haiku-4-5-20251001.

KEY ISOLATION (still as before)
  Reads ANTHROPIC_LABELING_KEY exclusively. The bridge wrapper at
  /home/workspace/logs/_signal_labeler.py aliases either ANTHROPIC_API_KEY
  env var OR the value in /home/workspace/.secrets/anthropic_labeling.key
  into ANTHROPIC_LABELING_KEY at invoke time.

COST GUARDRAIL
  Refuses to submit if estimated cost > MAX_COST_USD ($15).
  4,118 MCPs * ~1300 tokens * blended Haiku batch rate ~ $5.60. Comfortably
  under the ceiling.

IDEMPOTENT + REBOOT-RESILIENT
  - Re-runs skip MCPs already in signal_training_corpus for this teacher
  - In-flight batches resume from .batch_state.json on restart
  - Survives container recycles: state, key file, JSONL all on persistent fs
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.1"
WRITE_SERVICE = "http://127.0.0.1:8772"
ANTHROPIC_BASE = "https://api.anthropic.com"
MODEL_ID = "claude-haiku-4-5-20251001"

KEY_ENV_VAR = "ANTHROPIC_LABELING_KEY"

OUT_DIR        = Path("/home/workspace/shared/outputs/probes")
CORPUS_DIR     = Path("/home/workspace/shared/outputs/signal_training_corpus")
LOCK_PATH      = Path("/home/workspace/logs/_signal_labeler.lock")
STATE_PATH     = CORPUS_DIR / ".batch_state.json"
JSONL_PATH     = CORPUS_DIR / "signal_training_corpus.jsonl"
LOCK_TTL_SEC   = 86_400  # 24h -- a single batch lifecycle
POLL_INTERVAL_SEC = 90

MAX_COST_USD = 15.0
INPUT_COST_PER_MTOK_BATCH  = 0.50
OUTPUT_COST_PER_MTOK_BATCH = 2.50
EXPECTED_INPUT_TOKENS_PER_MCP  = 700
EXPECTED_OUTPUT_TOKENS_PER_MCP = 600
MAX_TOKENS_PER_REQUEST = 1500
MCP_LIMIT = None

# Pagination: write_service /query caps responses at 200 rows.
PAGE_SIZE = 200
MAX_PAGES = 100  # safety: 100 pages = 20,000 MCPs

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


# ---------------------------------------------------------------------------
# Lockfile + output
# ---------------------------------------------------------------------------

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


def write_probe_output(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"labeler_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


def append_jsonl(rows: list[dict]) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------

def ws_query(sql: str, timeout: int = 30) -> list:
    import requests
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": sql},
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception:
        pass
    return []


def ws_execute(sql: str, params: list | None = None, timeout: int = 30) -> bool:
    import requests
    payload: dict = {"sql": sql, "wait": True}
    if params is not None:
        payload["params"] = params
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/execute", json=payload, timeout=timeout
        )
        return r.status_code == 200
    except Exception:
        return False


def ws_write(table: str, rows: list[dict], timeout: int = 60) -> bool:
    import requests
    try:
        r = requests.post(
            f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=timeout,
        )
        return r.status_code == 200
    except Exception:
        return False


def bootstrap_corpus_table() -> bool:
    sql = """
        CREATE TABLE IF NOT EXISTS signal_training_corpus (
            id              BIGINT PRIMARY KEY DEFAULT nextval('seq_id'),
            server_id       VARCHAR NOT NULL,
            mcp_name        VARCHAR,
            signal_name     VARCHAR NOT NULL,
            signal_value    VARCHAR,
            signal_evidence TEXT,
            thought_process TEXT,
            overall_risk    VARCHAR,
            teacher_model   VARCHAR,
            teacher_run_id  VARCHAR,
            labeled_at      TIMESTAMPTZ DEFAULT now(),
            UNIQUE (server_id, signal_name, teacher_model)
        )
    """
    return ws_execute(sql)


def fetch_mcps_to_label(limit: int | None = None) -> list[dict]:
    """Pull MCPs that haven't been labeled yet by this teacher model.

    v1.1: Paginates through write_service /query in PAGE_SIZE-row chunks
    using LIMIT/OFFSET (write_service caps responses at 200 rows). Stops
    when a page returns fewer than PAGE_SIZE rows or MAX_PAGES reached.

    Idempotency: skips MCPs already in signal_training_corpus for this
    teacher_model via NOT EXISTS clause.
    """
    base_where = (
        "WHERE r.description IS NOT NULL "
        "AND length(r.description) > 30 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM signal_training_corpus c "
        "  WHERE c.server_id = r.server_id "
        "  AND c.teacher_model = '" + MODEL_ID + "'"
        ")"
    )
    base_sql = (
        "SELECT r.server_id, r.name, r.description, r.registry_source, r.url "
        "FROM mcp_server_registry r " + base_where + " "
        "ORDER BY r.server_id"
    )

    all_rows: list[dict] = []
    for page in range(MAX_PAGES):
        if limit is not None and len(all_rows) >= limit:
            break
        offset = page * PAGE_SIZE
        page_sql = f"{base_sql} LIMIT {PAGE_SIZE} OFFSET {offset}"
        rows = ws_query(page_sql)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break  # last page

    if limit is not None:
        all_rows = all_rows[:limit]
    return all_rows


# ---------------------------------------------------------------------------
# Prompt + parse
# ---------------------------------------------------------------------------

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


def parse_response_text(text: str) -> tuple[dict | None, str]:
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


# ---------------------------------------------------------------------------
# Anthropic Batches API
# ---------------------------------------------------------------------------

def anthropic_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def build_batch_request(mcp: dict, custom_id: str) -> dict:
    user_prompt = build_user_prompt(mcp)
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL_ID,
            "max_tokens": MAX_TOKENS_PER_REQUEST,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        },
    }


def submit_batch(api_key: str, requests_list: list[dict]) -> dict:
    import requests
    payload = {"requests": requests_list}
    r = requests.post(
        f"{ANTHROPIC_BASE}/v1/messages/batches",
        headers=anthropic_headers(api_key),
        json=payload,
        timeout=300,  # large batches take longer to upload
    )
    r.raise_for_status()
    return r.json()


def poll_batch(api_key: str, batch_id: str) -> dict:
    import requests
    r = requests.get(
        f"{ANTHROPIC_BASE}/v1/messages/batches/{batch_id}",
        headers=anthropic_headers(api_key),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def download_results(api_key: str, results_url: str) -> list[dict]:
    import requests
    r = requests.get(
        results_url, headers=anthropic_headers(api_key),
        timeout=600, stream=True,  # bigger batches = bigger result file
    )
    r.raise_for_status()
    out = []
    for line in r.iter_lines(decode_unicode=True):
        line = (line or "").strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


# ---------------------------------------------------------------------------
# Result ingestion
# ---------------------------------------------------------------------------

def ingest_results(
    results: list[dict],
    mcps_by_id: dict[str, dict],
    teacher_run_id: str,
) -> dict:
    rows_to_write: list[dict] = []
    jsonl_rows: list[dict] = []
    counts = {
        "total":          len(results),
        "succeeded":      0,
        "errored":        0,
        "parse_failed":   0,
        "signals_written": 0,
    }
    failed_examples: list[dict] = []

    for entry in results:
        custom_id = entry.get("custom_id", "")
        result = entry.get("result", {})
        result_type = result.get("type", "unknown")
        mcp = mcps_by_id.get(custom_id)
        if mcp is None:
            counts["errored"] += 1
            continue

        if result_type != "succeeded":
            counts["errored"] += 1
            if len(failed_examples) < 5:
                failed_examples.append({
                    "custom_id": custom_id,
                    "result_type": result_type,
                    "error": result.get("error"),
                })
            continue

        message = result.get("message", {})
        content_blocks = message.get("content", []) or []
        text = "".join(
            b.get("text", "") for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        parsed, perr = parse_response_text(text)
        if not parsed:
            counts["parse_failed"] += 1
            if len(failed_examples) < 5:
                failed_examples.append({
                    "custom_id": custom_id,
                    "parse_error": perr,
                    "text_preview": text[:300],
                })
            continue

        counts["succeeded"] += 1
        thought = parsed.get("thought_process", "")
        overall = parsed.get("overall_risk", "")
        signals_obj = parsed.get("signals", {})

        for sig_name in SIGNAL_NAMES:
            sig_payload = signals_obj.get(sig_name, {}) or {}
            value = sig_payload.get("value", "UNKNOWN")
            evidence = sig_payload.get("evidence", "")
            row = {
                "server_id":       mcp["server_id"],
                "mcp_name":        mcp.get("name", ""),
                "signal_name":     sig_name,
                "signal_value":    value,
                "signal_evidence": evidence,
                "thought_process": thought,
                "overall_risk":    overall,
                "teacher_model":   MODEL_ID,
                "teacher_run_id":  teacher_run_id,
            }
            rows_to_write.append(row)
            counts["signals_written"] += 1

        jsonl_rows.append({
            "server_id":   mcp["server_id"],
            "mcp_name":    mcp.get("name", ""),
            "description": mcp.get("description", ""),
            "label": {
                "thought_process": thought,
                "signals":         signals_obj,
                "overall_risk":    overall,
            },
            "teacher_model":  MODEL_ID,
            "teacher_run_id": teacher_run_id,
            "labeled_at":     datetime.now(timezone.utc).isoformat(),
        })

    if rows_to_write:
        for i in range(0, len(rows_to_write), 200):
            chunk = rows_to_write[i:i + 200]
            ws_write("signal_training_corpus", chunk)

    if jsonl_rows:
        append_jsonl(jsonl_rows)

    counts["failed_examples"] = failed_examples
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    started_iso = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not acquire_lock():
        write_probe_output({
            "probe": "signal_labeler", "version": VERSION,
            "started_at": started_iso,
            "verdict": "skipped_locked",
            "hostname": socket.gethostname(),
        })
        return 0

    try:
        api_key = os.environ.get(KEY_ENV_VAR, "")
        if not api_key:
            write_probe_output({
                "probe": "signal_labeler", "version": VERSION,
                "started_at": started_iso,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "missing_api_key",
                "reason": (
                    f"{KEY_ENV_VAR} not set. The wrapper at "
                    f"/home/workspace/logs/_signal_labeler.py should set this "
                    f"from ANTHROPIC_API_KEY env or .secrets file."
                ),
                "hostname": socket.gethostname(),
            })
            return 0

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        if not bootstrap_corpus_table():
            write_probe_output({
                "probe": "signal_labeler", "version": VERSION,
                "started_at": started_iso,
                "verdict": "bootstrap_failed",
                "hostname": socket.gethostname(),
            })
            return 0

        # Resume in-flight batch if there is one
        state = load_state()
        in_flight_batch_id = state.get("batch_id")
        if in_flight_batch_id:
            print(f"resuming in-flight batch {in_flight_batch_id}")
        else:
            # ----- NEW BATCH PATH -----
            mcps = fetch_mcps_to_label(limit=MCP_LIMIT)
            if not mcps:
                write_probe_output({
                    "probe": "signal_labeler", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": int((time.time() - t0) * 1000),
                    "verdict": "nothing_to_label",
                    "reason": (
                        "All eligible MCPs already labeled by "
                        f"teacher_model={MODEL_ID}."
                    ),
                    "hostname": socket.gethostname(),
                })
                return 0

            n = len(mcps)
            est_input_tokens  = n * EXPECTED_INPUT_TOKENS_PER_MCP
            est_output_tokens = n * EXPECTED_OUTPUT_TOKENS_PER_MCP
            est_cost = (
                (est_input_tokens  / 1_000_000) * INPUT_COST_PER_MTOK_BATCH +
                (est_output_tokens / 1_000_000) * OUTPUT_COST_PER_MTOK_BATCH
            )

            if est_cost > MAX_COST_USD:
                write_probe_output({
                    "probe": "signal_labeler", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "cost_guardrail_blocked",
                    "reason": (
                        f"Estimated cost ${est_cost:.2f} exceeds "
                        f"MAX_COST_USD=${MAX_COST_USD:.2f}. Edit MCP_LIMIT or "
                        f"MAX_COST_USD to override."
                    ),
                    "mcps_eligible": n,
                    "estimated_cost_usd": round(est_cost, 4),
                    "hostname": socket.gethostname(),
                })
                return 0

            # Build batch requests
            requests_list: list[dict] = []
            mcps_by_id: dict[str, dict] = {}
            for i, mcp in enumerate(mcps):
                custom_id = f"mcp_{i:06d}_{mcp['server_id'][:32]}"
                custom_id = "".join(
                    c if (c.isalnum() or c in "_-") else "_"
                    for c in custom_id
                )[:64]
                mcps_by_id[custom_id] = mcp
                requests_list.append(build_batch_request(mcp, custom_id))

            # Submit
            try:
                batch = submit_batch(api_key, requests_list)
            except Exception as e:
                write_probe_output({
                    "probe": "signal_labeler", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "submit_failed",
                    "error": f"{type(e).__name__}: {e}",
                    "mcps_eligible": n,
                    "hostname": socket.gethostname(),
                })
                return 0

            in_flight_batch_id = batch.get("id")
            teacher_run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            state = {
                "batch_id":        in_flight_batch_id,
                "submitted_at":    datetime.now(timezone.utc).isoformat(),
                "mcps_by_id":      mcps_by_id,
                "teacher_run_id":  teacher_run_id,
                "estimated_cost_usd": round(est_cost, 4),
                "mcps_count":      n,
            }
            save_state(state)
            print(
                f"submitted batch {in_flight_batch_id} "
                f"({n} MCPs, est ${est_cost:.2f})"
            )

        # Poll until done
        mcps_by_id = state["mcps_by_id"]
        teacher_run_id = state["teacher_run_id"]
        last_status = None
        polls = 0
        deadline = time.time() + 24 * 3600
        while time.time() < deadline:
            polls += 1
            try:
                batch = poll_batch(api_key, in_flight_batch_id)
            except Exception as e:
                print(f"poll error: {type(e).__name__}: {e}")
                time.sleep(POLL_INTERVAL_SEC)
                continue
            status = batch.get("processing_status", "unknown")
            counts = batch.get("request_counts", {}) or {}
            if status != last_status:
                print(f"poll {polls}: status={status} counts={counts}")
                last_status = status
            if status == "ended":
                break
            time.sleep(POLL_INTERVAL_SEC)
        else:
            write_probe_output({
                "probe": "signal_labeler", "version": VERSION,
                "started_at": started_iso,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "poll_timeout",
                "batch_id": in_flight_batch_id,
                "reason": "Batch did not reach 'ended' state within 24h SLA.",
                "hostname": socket.gethostname(),
            })
            return 0

        results_url = batch.get("results_url")
        if not results_url:
            write_probe_output({
                "probe": "signal_labeler", "version": VERSION,
                "started_at": started_iso,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "no_results_url",
                "batch_id": in_flight_batch_id,
                "batch_object": batch,
                "hostname": socket.gethostname(),
            })
            return 0

        results = download_results(api_key, results_url)
        ingest_counts = ingest_results(
            results, mcps_by_id, teacher_run_id
        )

        save_state({
            "last_batch_id":     in_flight_batch_id,
            "last_finished_at":  datetime.now(timezone.utc).isoformat(),
            "last_counts":       ingest_counts,
            "last_run_id":       teacher_run_id,
        })

        out = write_probe_output({
            "probe":          "signal_labeler",
            "version":        VERSION,
            "started_at":     started_iso,
            "finished_at":    datetime.now(timezone.utc).isoformat(),
            "duration_ms":    int((time.time() - t0) * 1000),
            "verdict":        "ok" if ingest_counts["succeeded"] > 0 else "all_failed",
            "batch_id":       in_flight_batch_id,
            "teacher_model":  MODEL_ID,
            "teacher_run_id": teacher_run_id,
            "counts":         ingest_counts,
            "jsonl_path":     str(JSONL_PATH),
            "hostname":       socket.gethostname(),
        })
        print(
            f"signal_labeler -> ok  succeeded={ingest_counts['succeeded']} "
            f"errored={ingest_counts['errored']} parse_failed={ingest_counts['parse_failed']} "
            f"output={out}"
        )
        return 0

    except Exception as e:
        import traceback
        write_probe_output({
            "probe": "signal_labeler", "version": VERSION,
            "started_at": started_iso,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.time() - t0) * 1000),
            "verdict": "probe_exception",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[:3000],
            "hostname": socket.gethostname(),
        })
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())