#!/usr/bin/env python3
"""
signal_labeler_sonnet.py v1.0  (2026-04-30)

Frontier-quality re-label of every MCP in mcp_server_registry using
claude-sonnet-4-5 via Anthropic Message Batches API. Adds rows to
signal_training_corpus with teacher_model='claude-sonnet-4-5' alongside
the existing Haiku 4.5 rows -- the UNIQUE (server_id, signal_name,
teacher_model) constraint keeps both teachers' labels.

WHY
  Spot-check found systematic ONE-NOTCH-MORE-CAUTIOUS pattern: Haiku
  scored LOW where Sonnet scored MEDIUM, MEDIUM where Sonnet scored HIGH.
  100% neighbour agreement, but Haiku misses real catches like
  shadertoy=BROAD exploit_surface (arbitrary GLSL execution).
  For a SECURITY tool we want the Sonnet labels.

COST
  4,157 MCPs * ~1300 tokens * Sonnet 4.5 batch rates ($1.50/M in, $7.50/M out)
  = ~$23 expected. MAX_COST_USD ceiling = $30 (headroom for token estimate slip).

BASED ON signal_labeler.py v1.1. Differences:
  - MODEL_ID = 'claude-sonnet-4-5'
  - Pricing constants for Sonnet 4.5 batch ($1.50/$7.50 vs Haiku $0.50/$2.50)
  - Independent state file (.batch_state_sonnet.json) so won't collide
  - Independent lock file (_signal_labeler_sonnet.lock)
  - Independent JSONL mirror (signal_training_corpus_sonnet.jsonl)
  - MAX_COST_USD raised to $30
  - fetch_mcps_to_label() skips MCPs already labeled by 'claude-sonnet-4-5'
    (idempotent on re-run; preserves existing Haiku rows by querying by
    teacher_model = MODEL_ID)
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.0"
WRITE_SERVICE = "http://127.0.0.1:8772"
ANTHROPIC_BASE = "https://api.anthropic.com"
MODEL_ID = "claude-sonnet-4-5"  # FRONTIER teacher

KEY_ENV_VAR = "ANTHROPIC_LABELING_KEY"

OUT_DIR        = Path("/home/workspace/shared/outputs/probes")
CORPUS_DIR     = Path("/home/workspace/shared/outputs/signal_training_corpus")
LOCK_PATH      = Path("/home/workspace/logs/_signal_labeler_sonnet.lock")
STATE_PATH     = CORPUS_DIR / ".batch_state_sonnet.json"
JSONL_PATH     = CORPUS_DIR / "signal_training_corpus_sonnet.jsonl"
LOCK_TTL_SEC   = 86_400
POLL_INTERVAL_SEC = 90

MAX_COST_USD = 30.0  # raised for Sonnet pricing
# Sonnet 4.5 batch pricing (50% off standard $3/M input, $15/M output)
INPUT_COST_PER_MTOK_BATCH  = 1.50
OUTPUT_COST_PER_MTOK_BATCH = 7.50
EXPECTED_INPUT_TOKENS_PER_MCP  = 700
EXPECTED_OUTPUT_TOKENS_PER_MCP = 600
MAX_TOKENS_PER_REQUEST = 1500
MCP_LIMIT = None

PAGE_SIZE = 200
MAX_PAGES = 100

# Same signal definitions as Haiku labeler (must match for comparability)
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
    try:
        LOCK_PATH.unlink()
    except Exception:
        pass


def write_probe_output(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"labeler_sonnet_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


def append_jsonl(rows: list[dict]) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


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


def ws_query(sql: str, timeout: int = 30) -> list:
    import requests
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=timeout)
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
        r = requests.post(f"{WRITE_SERVICE}/execute", json=payload, timeout=timeout)
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
    """Pull MCPs that don't yet have Sonnet labels.

    Critical: NOT EXISTS clause filters by teacher_model='claude-sonnet-4-5'
    NOT the Haiku model ID. So Haiku-labeled MCPs are still candidates for
    Sonnet labeling. Only re-runs of THIS labeler skip already-Sonnet-done.
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
        "FROM mcp_server_registry r " + base_where + " ORDER BY r.server_id"
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
            break
    if limit is not None:
        all_rows = all_rows[:limit]
    return all_rows


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


def anthropic_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def build_batch_request(mcp: dict, custom_id: str) -> dict:
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL_ID,
            "max_tokens": MAX_TOKENS_PER_REQUEST,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(mcp)}],
            "temperature": 0.2,
        },
    }


def submit_batch(api_key: str, requests_list: list[dict]) -> dict:
    import requests
    r = requests.post(
        f"{ANTHROPIC_BASE}/v1/messages/batches",
        headers=anthropic_headers(api_key),
        json={"requests": requests_list},
        timeout=300,
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
        timeout=600, stream=True,
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


def ingest_results(
    results: list[dict],
    mcps_by_id: dict[str, dict],
    teacher_run_id: str,
) -> dict:
    rows_to_write: list[dict] = []
    jsonl_rows: list[dict] = []
    counts = {"total": len(results), "succeeded": 0, "errored": 0,
              "parse_failed": 0, "signals_written": 0}
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


def main() -> int:
    started_iso = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not acquire_lock():
        write_probe_output({
            "probe": "signal_labeler_sonnet", "version": VERSION,
            "started_at": started_iso,
            "verdict": "skipped_locked",
            "hostname": socket.gethostname(),
        })
        return 0

    try:
        api_key = os.environ.get(KEY_ENV_VAR, "")
        if not api_key:
            write_probe_output({
                "probe": "signal_labeler_sonnet", "version": VERSION,
                "started_at": started_iso,
                "verdict": "missing_api_key",
                "hostname": socket.gethostname(),
            })
            return 0

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        if not bootstrap_corpus_table():
            write_probe_output({
                "probe": "signal_labeler_sonnet", "version": VERSION,
                "started_at": started_iso,
                "verdict": "bootstrap_failed",
                "hostname": socket.gethostname(),
            })
            return 0

        state = load_state()
        in_flight_batch_id = state.get("batch_id")
        if in_flight_batch_id:
            print(f"resuming in-flight batch {in_flight_batch_id}")
        else:
            mcps = fetch_mcps_to_label(limit=MCP_LIMIT)
            if not mcps:
                write_probe_output({
                    "probe": "signal_labeler_sonnet", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "nothing_to_label",
                    "reason": f"All eligible MCPs already labeled by {MODEL_ID}.",
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
                    "probe": "signal_labeler_sonnet", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "cost_guardrail_blocked",
                    "reason": (
                        f"Estimated cost ${est_cost:.2f} exceeds "
                        f"MAX_COST_USD=${MAX_COST_USD:.2f}."
                    ),
                    "mcps_eligible": n,
                    "estimated_cost_usd": round(est_cost, 4),
                    "hostname": socket.gethostname(),
                })
                return 0

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

            try:
                batch = submit_batch(api_key, requests_list)
            except Exception as e:
                write_probe_output({
                    "probe": "signal_labeler_sonnet", "version": VERSION,
                    "started_at": started_iso,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "submit_failed",
                    "error": f"{type(e).__name__}: {e}",
                    "mcps_eligible": n,
                    "hostname": socket.gethostname(),
                })
                return 0

            in_flight_batch_id = batch.get("id")
            teacher_run_id = f"run_sonnet_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            state = {
                "batch_id":           in_flight_batch_id,
                "submitted_at":       datetime.now(timezone.utc).isoformat(),
                "mcps_by_id":         mcps_by_id,
                "teacher_run_id":     teacher_run_id,
                "estimated_cost_usd": round(est_cost, 4),
                "mcps_count":         n,
            }
            save_state(state)
            print(f"submitted batch {in_flight_batch_id} ({n} MCPs, est ${est_cost:.2f})")

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
                "probe": "signal_labeler_sonnet", "version": VERSION,
                "started_at": started_iso,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "poll_timeout",
                "batch_id": in_flight_batch_id,
                "hostname": socket.gethostname(),
            })
            return 0

        results_url = batch.get("results_url")
        if not results_url:
            write_probe_output({
                "probe": "signal_labeler_sonnet", "version": VERSION,
                "started_at": started_iso,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "no_results_url",
                "batch_id": in_flight_batch_id,
                "hostname": socket.gethostname(),
            })
            return 0

        results = download_results(api_key, results_url)
        ingest_counts = ingest_results(results, mcps_by_id, teacher_run_id)

        save_state({
            "last_batch_id":     in_flight_batch_id,
            "last_finished_at":  datetime.now(timezone.utc).isoformat(),
            "last_counts":       ingest_counts,
            "last_run_id":       teacher_run_id,
        })

        out = write_probe_output({
            "probe":          "signal_labeler_sonnet",
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
        print(f"signal_labeler_sonnet -> ok succeeded={ingest_counts['succeeded']} "
              f"errored={ingest_counts['errored']} parse_failed={ingest_counts['parse_failed']}")
        return 0

    except Exception as e:
        import traceback
        write_probe_output({
            "probe": "signal_labeler_sonnet", "version": VERSION,
            "started_at": started_iso,
            "finished_at": datetime.now(timezone.utc).isoformat(),
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