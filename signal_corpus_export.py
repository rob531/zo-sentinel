#!/usr/bin/env python3
"""
signal_corpus_export.py v1.2  (2026-04-30)

v1.2: Retry on empty pages + verify against COUNT(*) total. v1.1 silently
      truncated when write_service returned a transient empty page mid-stream
      (got 1,800 rows of 51,220).
v1.1: MAX_PAGES bumped 200 -> 500.

Export signal_training_corpus to HuggingFace SFT-ready JSONL files.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.2"
WRITE_SERVICE = "http://127.0.0.1:8772"
OUT_DIR = Path("/home/workspace/shared/outputs/probes")
CORPUS_OUT = Path("/home/workspace/shared/outputs/signal_training_corpus/sft_corpus")
LOCK_PATH = Path("/home/workspace/logs/_signal_corpus_export.lock")
LOCK_TTL_SEC = 3_600

HAIKU_MODEL  = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5"

TRAIN_SPLIT_THRESHOLD = 0.9
SPLIT_HASH_SEED = b"zomesh-sft-v1-2026-04-30"

PAGE_SIZE = 200
MAX_PAGES = 500
MAX_RETRIES_PER_PAGE = 5
RETRY_BACKOFF_BASE_SEC = 0.5

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
    out_path = OUT_DIR / f"corpus_export_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


def ws_query(sql: str, timeout: int = 60) -> list | None:
    """Returns rows on success, None on transport error.  Distinguishes
    'no rows' (returns []) from 'request failed' (returns None) so callers
    can retry only on actual failures."""
    import requests
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("rows", [])
        return None
    except Exception:
        return None


def ws_query_count(table: str, where: str = "") -> int:
    """Run a COUNT(*) and return the integer."""
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    rows = ws_query(sql)
    if not rows:
        return -1
    return int(rows[0].get("n", -1))


def ws_query_paginated_with_target(
    base_sql_no_limit: str,
    expected_total: int | None = None,
) -> tuple[list, dict]:
    """Paginate with retries.  If expected_total is given, keep trying pages
    until we have that many rows OR exhaust MAX_PAGES.

    Returns (rows, diagnostics).
    Empty page = retry up to MAX_RETRIES_PER_PAGE.
    Genuine end-of-data = page returns < PAGE_SIZE rows AFTER having returned
    rows on this attempt OR retries exhausted with empty results AND we're past
    expected_total (if known).
    """
    rows: list = []
    diagnostics = {
        "pages_fetched":   0,
        "empty_retries":   0,
        "transport_errors": 0,
        "early_exit_reason": "",
        "expected_total":  expected_total,
    }

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        page_sql = f"{base_sql_no_limit} LIMIT {PAGE_SIZE} OFFSET {offset}"

        page_rows = None
        for attempt in range(MAX_RETRIES_PER_PAGE):
            page_rows = ws_query(page_sql)
            if page_rows is None:
                diagnostics["transport_errors"] += 1
                time.sleep(RETRY_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            if len(page_rows) > 0:
                break
            # Empty result: might be end-of-data, might be a flake.
            # If we know expected_total and we're already there, accept.
            if expected_total is not None and len(rows) >= expected_total:
                break
            # Otherwise retry briefly.
            diagnostics["empty_retries"] += 1
            time.sleep(RETRY_BACKOFF_BASE_SEC * (2 ** attempt))

        if page_rows is None:
            diagnostics["early_exit_reason"] = (
                f"transport errors exhausted retries at page {page} (offset {offset})"
            )
            break

        if len(page_rows) == 0:
            # Genuine end-of-data IF we have at least expected_total rows or
            # we've never been told to expect any.
            if expected_total is None or len(rows) >= expected_total:
                diagnostics["early_exit_reason"] = f"empty page at offset {offset} (treated as end of data)"
            else:
                diagnostics["early_exit_reason"] = (
                    f"empty page at offset {offset} after retries; only {len(rows)} of "
                    f"expected {expected_total} rows fetched"
                )
            break

        rows.extend(page_rows)
        diagnostics["pages_fetched"] += 1

        # Genuine end-of-data: page returned fewer rows than PAGE_SIZE AND
        # we have at least expected_total (or no expectation).
        if len(page_rows) < PAGE_SIZE:
            if expected_total is None or len(rows) >= expected_total:
                diagnostics["early_exit_reason"] = (
                    f"short page at offset {offset} ({len(page_rows)} rows) -- end of data"
                )
                break
            # Otherwise keep going -- short page might be a flake.

        # If we've hit the expected total exactly, stop.
        if expected_total is not None and len(rows) >= expected_total:
            diagnostics["early_exit_reason"] = (
                f"reached expected_total={expected_total} at offset {offset}"
            )
            break

    if not diagnostics["early_exit_reason"]:
        diagnostics["early_exit_reason"] = f"hit MAX_PAGES={MAX_PAGES}"
    return rows, diagnostics


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


def reconstruct_assistant_json(rows: list[dict]) -> str:
    if not rows:
        return ""
    first = rows[0]
    signals_obj: dict = {}
    for r in rows:
        sig_name = r.get("signal_name")
        if sig_name not in SIGNAL_NAMES:
            continue
        signals_obj[sig_name] = {
            "value":    r.get("signal_value", "UNKNOWN"),
            "evidence": r.get("signal_evidence", ""),
        }
    output = {
        "thought_process": first.get("thought_process", ""),
        "signals":         signals_obj,
        "overall_risk":    first.get("overall_risk", ""),
    }
    return json.dumps(output, separators=(",", ":"))


def split_for_server_id(server_id: str) -> str:
    h = hashlib.blake2s(
        SPLIT_HASH_SEED + server_id.encode("utf-8"),
        digest_size=8,
    ).digest()
    val = int.from_bytes(h, "big") / (1 << 64)
    return "train" if val < TRAIN_SPLIT_THRESHOLD else "eval-overlap"


def build_chat_record(mcp: dict, assistant_text: str, teacher_model: str, split: str) -> dict:
    return {
        "messages": [
            {"role": "system",    "content": SYSTEM_PROMPT},
            {"role": "user",     "content": build_user_prompt(mcp)},
            {"role": "assistant", "content": assistant_text},
        ],
        "metadata": {
            "server_id":     mcp.get("server_id", ""),
            "mcp_name":      mcp.get("name", ""),
            "teacher_model": teacher_model,
            "split":         split,
        },
    }


def main() -> int:
    started_iso = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not acquire_lock():
        write_probe_output({
            "probe": "signal_corpus_export", "version": VERSION,
            "started_at": started_iso,
            "verdict": "skipped_locked",
            "hostname": socket.gethostname(),
        })
        return 0

    try:
        CORPUS_OUT.mkdir(parents=True, exist_ok=True)

        # Get expected totals upfront so paginators know when they're 'done'.
        expected_corpus_rows  = ws_query_count("signal_training_corpus")
        expected_metadata_mcps = ws_query_count(
            "mcp_server_registry",
            "server_id IN (SELECT DISTINCT server_id FROM signal_training_corpus)",
        )
        print(f"expected: {expected_corpus_rows} corpus rows, {expected_metadata_mcps} metadata rows")

        print("fetching MCP metadata...")
        mcps_rows, meta_diag = ws_query_paginated_with_target(
            "SELECT r.server_id, r.name, r.description, r.registry_source, r.url "
            "FROM mcp_server_registry r "
            "WHERE r.server_id IN ("
            "  SELECT DISTINCT server_id FROM signal_training_corpus"
            ") ORDER BY r.server_id",
            expected_total=expected_metadata_mcps if expected_metadata_mcps > 0 else None,
        )
        mcp_by_id: dict[str, dict] = {row["server_id"]: row for row in mcps_rows}
        print(f"  metadata for {len(mcp_by_id)} MCPs ({meta_diag})")

        print("fetching signal_training_corpus rows...")
        corpus_rows, corpus_diag = ws_query_paginated_with_target(
            "SELECT server_id, teacher_model, signal_name, signal_value, "
            "signal_evidence, thought_process, overall_risk "
            "FROM signal_training_corpus "
            "ORDER BY server_id, teacher_model, signal_name",
            expected_total=expected_corpus_rows if expected_corpus_rows > 0 else None,
        )
        print(f"  {len(corpus_rows)} corpus rows ({corpus_diag})")

        if expected_corpus_rows > 0 and len(corpus_rows) < expected_corpus_rows * 0.95:
            print(f"  WARNING: corpus pull only got {len(corpus_rows)}/{expected_corpus_rows} "
                  f"({100.0 * len(corpus_rows) / expected_corpus_rows:.1f}%)")

        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for row in corpus_rows:
            key = (row["server_id"], row["teacher_model"])
            grouped[key].append(row)

        haiku_ids  = {sid for (sid, m) in grouped if m == HAIKU_MODEL}
        sonnet_ids = {sid for (sid, m) in grouped if m == SONNET_MODEL}
        both_ids       = haiku_ids & sonnet_ids
        sonnet_only    = sonnet_ids - haiku_ids
        haiku_only     = haiku_ids - sonnet_ids
        print(f"  both teachers: {len(both_ids)}")
        print(f"  sonnet only:   {len(sonnet_only)}")
        print(f"  haiku only:    {len(haiku_only)} (dropped from export)")

        train_ids:        list[str] = []
        eval_overlap_ids: list[str] = []
        for sid in sorted(both_ids):
            if split_for_server_id(sid) == "train":
                train_ids.append(sid)
            else:
                eval_overlap_ids.append(sid)
        eval_novel_ids = sorted(sonnet_only)
        print(f"  train:         {len(train_ids)}")
        print(f"  eval-overlap:  {len(eval_overlap_ids)}")
        print(f"  eval-novel:    {len(eval_novel_ids)}")

        splits_json = {
            "version": VERSION,
            "split_hash_seed": SPLIT_HASH_SEED.decode(),
            "train_split_threshold": TRAIN_SPLIT_THRESHOLD,
            "counts": {
                "both_teachers":  len(both_ids),
                "sonnet_only":    len(sonnet_only),
                "haiku_only_dropped": len(haiku_only),
                "train":          len(train_ids),
                "eval_overlap":   len(eval_overlap_ids),
                "eval_novel":     len(eval_novel_ids),
            },
            "server_ids": {
                "train":         train_ids,
                "eval_overlap":  eval_overlap_ids,
                "eval_novel":    eval_novel_ids,
                "haiku_only_dropped": sorted(haiku_only),
            },
            "diagnostics": {
                "expected_corpus_rows":  expected_corpus_rows,
                "actual_corpus_rows":    len(corpus_rows),
                "expected_metadata":     expected_metadata_mcps,
                "actual_metadata":       len(mcp_by_id),
                "corpus_pull":           corpus_diag,
                "metadata_pull":         meta_diag,
            },
        }
        (CORPUS_OUT / "splits.json").write_text(json.dumps(splits_json, indent=2))

        files_to_build = [
            {"path": CORPUS_OUT / "train_haiku.jsonl",          "ids": train_ids,        "teacher": HAIKU_MODEL,  "split": "train"},
            {"path": CORPUS_OUT / "train_sonnet.jsonl",         "ids": train_ids,        "teacher": SONNET_MODEL, "split": "train"},
            {"path": CORPUS_OUT / "eval_overlap_haiku.jsonl",   "ids": eval_overlap_ids, "teacher": HAIKU_MODEL,  "split": "eval-overlap"},
            {"path": CORPUS_OUT / "eval_overlap_sonnet.jsonl",  "ids": eval_overlap_ids, "teacher": SONNET_MODEL, "split": "eval-overlap"},
            {"path": CORPUS_OUT / "eval_novel_sonnet.jsonl",    "ids": eval_novel_ids,   "teacher": SONNET_MODEL, "split": "eval-novel"},
        ]

        file_stats: list[dict] = []
        for spec in files_to_build:
            written = 0
            skipped_no_meta = 0
            skipped_incomplete = 0
            with spec["path"].open("w", encoding="utf-8") as f:
                for sid in spec["ids"]:
                    mcp = mcp_by_id.get(sid)
                    if mcp is None:
                        skipped_no_meta += 1
                        continue
                    rows = grouped.get((sid, spec["teacher"]), [])
                    if len(rows) < len(SIGNAL_NAMES):
                        skipped_incomplete += 1
                        continue
                    assistant_text = reconstruct_assistant_json(rows)
                    record = build_chat_record(mcp, assistant_text, spec["teacher"], spec["split"])
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
                    written += 1
            stat = {
                "file":               str(spec["path"].name),
                "teacher":            spec["teacher"],
                "split":              spec["split"],
                "records_written":    written,
                "skipped_no_meta":    skipped_no_meta,
                "skipped_incomplete": skipped_incomplete,
                "size_bytes":         spec["path"].stat().st_size,
            }
            file_stats.append(stat)
            print(f"  wrote {spec['path'].name}: {written} records, {stat['size_bytes']:,} bytes")

        manifest = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
            "corpus_dir": str(CORPUS_OUT),
            "split_hash_seed": SPLIT_HASH_SEED.decode(),
            "experimental_setups": {
                "v1_sonnet_only_sft": {
                    "description": "Single-stage SFT on Sonnet 4.5 frontier teacher labels.",
                    "train_files": ["train_sonnet.jsonl"],
                    "eval_files": {
                        "primary_gold":      "eval_overlap_sonnet.jsonl",
                        "cross_teacher":     "eval_overlap_haiku.jsonl",
                        "novel_generalize":  "eval_novel_sonnet.jsonl",
                    },
                },
                "v2_curriculum": {
                    "description": "Two-stage SFT: pretrain on Haiku, fine-tune on Sonnet.",
                    "stage1_files": ["train_haiku.jsonl"],
                    "stage2_files": ["train_sonnet.jsonl"],
                    "eval_files": {
                        "primary_gold":      "eval_overlap_sonnet.jsonl",
                        "cross_teacher":     "eval_overlap_haiku.jsonl",
                        "novel_generalize":  "eval_novel_sonnet.jsonl",
                    },
                },
            },
            "file_stats": file_stats,
            "counts": splits_json["counts"],
            "diagnostics": splits_json["diagnostics"],
            "system_prompt": SYSTEM_PROMPT,
            "signal_names": SIGNAL_NAMES,
            "teachers": {
                "haiku":  HAIKU_MODEL,
                "sonnet": SONNET_MODEL,
            },
        }
        (CORPUS_OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

        out = write_probe_output({
            "probe":         "signal_corpus_export",
            "version":       VERSION,
            "started_at":    started_iso,
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "duration_ms":   int((time.time() - t0) * 1000),
            "verdict":       "ok",
            "corpus_dir":    str(CORPUS_OUT),
            "counts":        splits_json["counts"],
            "diagnostics":   splits_json["diagnostics"],
            "file_stats":    file_stats,
            "hostname":      socket.gethostname(),
        })
        print(f"signal_corpus_export -> ok: {out}")
        return 0

    except Exception as e:
        import traceback
        write_probe_output({
            "probe": "signal_corpus_export", "version": VERSION,
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