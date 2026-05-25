#!/usr/bin/env python3
"""
signal_spot_check.py v1.0  (2026-04-30)

Validates Haiku 4.5 teacher labels against Sonnet 4.5 (frontier) on a
stratified sample of 50 MCPs from signal_training_corpus.

METHOD
  1. Stratified sample: 10 LOW + 20 MEDIUM + 15 HIGH + 5 CRITICAL
  2. For each MCP, send same prompt to Sonnet 4.5 synchronously
  3. Parse Sonnet's JSON response with same schema
  4. Compute agreement per signal (strict + neighbour-tolerant for ordinal)
  5. Surface up to 10 disagreement examples for inspection

COST: ~$0.56 (50 MCPs * ~1300 tokens * Sonnet 4.5 blended rate)
WALL TIME: 5-10 min sequential (no batch -- batch overhead not worth it for 50)

MODEL: claude-sonnet-4-5 (frontier, 3x cost of Haiku 4.5)

OUTPUT: /home/workspace/shared/outputs/probes/signal_spot_check_<TS>.json
  with overall agreement rates, per-signal breakdown, and disagreement
  examples for manual review.

FIRES VIA: trigger_watcher -> run_bootstrap_signal_spot_check.request
"""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.0"
WRITE_SERVICE = "http://127.0.0.1:8772"
ANTHROPIC_BASE = "https://api.anthropic.com"
VALIDATION_MODEL = "claude-sonnet-4-5"  # frontier, used to validate Haiku
TEACHER_MODEL    = "claude-haiku-4-5-20251001"  # the model we are validating

KEY_ENV_VAR = "ANTHROPIC_LABELING_KEY"

OUT_DIR    = Path("/home/workspace/shared/outputs/probes")
LOCK_PATH  = Path("/home/workspace/logs/_signal_spot_check.lock")
LOCK_TTL_SEC = 1800  # 30 min ceiling for the probe

# Stratified sample sizes per overall_risk bucket
SAMPLE_PLAN = {"LOW": 10, "MEDIUM": 20, "HIGH": 15, "CRITICAL": 5}
SEED = 42

MAX_TOKENS = 1500

# Same signal definitions as production labeler (CRITICAL: must stay in sync)
SIGNALS = [
    {
        "name": "auth_strength",
        "definition": "How securely the MCP authenticates clients. Values: STRONG (mTLS, OAuth with PKCE, hardware tokens), MODERATE (OAuth with secrets, session tokens), WEAK (API keys, basic auth, no auth), UNKNOWN.",
        "values": ["STRONG", "MODERATE", "WEAK", "UNKNOWN"],
        "ordinal": True,  # STRONG..WEAK is monotone risk
    },
    {
        "name": "capability_breadth",
        "definition": "How broad the tool's capabilities are. NARROW (one specific function), MODERATE (a focused domain), BROAD (general-purpose / many tools), UNKNOWN.",
        "values": ["NARROW", "MODERATE", "BROAD", "UNKNOWN"],
        "ordinal": True,
    },
    {
        "name": "data_sensitivity",
        "definition": "Sensitivity of data the MCP handles. PUBLIC (open data, web), INTERNAL (org data, low-risk), SENSITIVE (PII, secrets, financial), CRITICAL (auth tokens, credentials, prod data), UNKNOWN.",
        "values": ["PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"],
        "ordinal": True,
    },
    {
        "name": "network_egress",
        "definition": "Whether the MCP can reach external networks. NONE (local-only), INTERNAL (intranet only), EXTERNAL (internet), ARBITRARY (user-controlled URLs / SSRF risk), UNKNOWN.",
        "values": ["NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"],
        "ordinal": True,
    },
    {
        "name": "maintainer_trust",
        "definition": "Trustworthiness of the maintainer. ESTABLISHED (well-known org, long history), VERIFIED (signed packages, GitHub-verified org), COMMUNITY (active OSS project, multiple contributors), UNKNOWN_AUTHOR (unknown individual or empty signals), SUSPICIOUS (red flags: typosquat, sudden ownership change, malicious history).",
        "values": ["ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR", "SUSPICIOUS"],
        "ordinal": True,
    },
    {
        "name": "exploit_surface",
        "definition": "Estimated exploit surface for an attacker. MINIMAL (read-only, sandboxed), LIMITED (writes to scoped resources), MODERATE (multiple capabilities, some elevated), BROAD (file system, command execution, or arbitrary code paths), UNKNOWN.",
        "values": ["MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN"],
        "ordinal": True,
    },
]
SIGNAL_NAMES = [s["name"] for s in SIGNALS]
SIGNAL_VALUES = {s["name"]: s["values"] for s in SIGNALS}

OVERALL_RISK_VALUES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

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
    out_path = OUT_DIR / f"signal_spot_check_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    return out_path


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


def sample_stratified() -> list[dict]:
    """Pull stratified sample of MCPs from signal_training_corpus joined to
    mcp_server_registry. Returns one row per MCP with all 6 Haiku labels.
    """
    random.seed(SEED)
    sample: list[dict] = []

    for risk_bucket, n_target in SAMPLE_PLAN.items():
        # Pull a candidate pool 4x the target then random sample
        pool_size = n_target * 4
        sql = f"""
            SELECT DISTINCT c.server_id, c.mcp_name, c.overall_risk,
                   c.thought_process,
                   r.description, r.registry_source, r.url
            FROM signal_training_corpus c
            JOIN mcp_server_registry r ON r.server_id = c.server_id
            WHERE c.signal_name = 'auth_strength'
              AND c.teacher_model = '{TEACHER_MODEL}'
              AND c.overall_risk = '{risk_bucket}'
              AND r.description IS NOT NULL
            ORDER BY c.server_id
            LIMIT {pool_size}
        """
        pool = ws_query(sql)
        if len(pool) < n_target:
            print(f"  warning: only {len(pool)} MCPs in {risk_bucket} pool, taking all")
            chosen = pool
        else:
            chosen = random.sample(pool, n_target)
        sample.extend(chosen)

    # Now hydrate each with all 6 Haiku signal labels
    server_ids = [m["server_id"] for m in sample]
    if not server_ids:
        return []
    quoted_ids = ",".join(f"'{sid}'" for sid in server_ids)
    label_sql = f"""
        SELECT server_id, signal_name, signal_value, signal_evidence
        FROM signal_training_corpus
        WHERE teacher_model = '{TEACHER_MODEL}'
          AND server_id IN ({quoted_ids})
    """
    label_rows = ws_query(label_sql)
    labels_by_mcp: dict[str, dict] = {}
    for row in label_rows:
        sid = row["server_id"]
        labels_by_mcp.setdefault(sid, {})[row["signal_name"]] = {
            "value":    row["signal_value"],
            "evidence": row["signal_evidence"],
        }
    for mcp in sample:
        mcp["haiku_signals"] = labels_by_mcp.get(mcp["server_id"], {})
    return sample


def build_user_prompt(mcp: dict) -> str:
    signals_block = "\n".join(
        f"  {i+1}. {s['name']}: {s['definition']}"
        f"\n     Values: {' | '.join(s['values'])}"
        for i, s in enumerate(SIGNALS)
    )
    return (
        f"MCP SERVER UNDER REVIEW:\n"
        f"  server_id: {mcp.get('server_id', '?')}\n"
        f"  name:      {mcp.get('mcp_name', '?')}\n"
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


def call_sonnet(api_key: str, mcp: dict, timeout: int = 60) -> tuple[dict | None, str]:
    import requests
    payload = {
        "model": VALIDATION_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_user_prompt(mcp)}],
        "temperature": 0.2,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        r = requests.post(
            f"{ANTHROPIC_BASE}/v1/messages",
            headers=headers, json=payload, timeout=timeout,
        )
        if r.status_code != 200:
            return None, f"http {r.status_code}: {r.text[:300]}"
        body = r.json()
        text = "".join(
            b.get("text", "") for b in body.get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
        parsed, perr = parse_response_text(text)
        if not parsed:
            return None, f"parse: {perr}"
        return parsed, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def neighbour_match(signal_name: str, a: str, b: str) -> bool:
    """Off-by-one tolerance for ordinal signals."""
    if a == b:
        return True
    values = SIGNAL_VALUES.get(signal_name, [])
    if a not in values or b not in values:
        return False
    return abs(values.index(a) - values.index(b)) <= 1


def overall_risk_neighbour(a: str, b: str) -> bool:
    if a == b:
        return True
    if a not in OVERALL_RISK_VALUES or b not in OVERALL_RISK_VALUES:
        return False
    return abs(OVERALL_RISK_VALUES.index(a) - OVERALL_RISK_VALUES.index(b)) <= 1


def compute_agreement(comparisons: list[dict]) -> dict:
    """Per-signal exact + neighbour agreement, and overall_risk agreement."""
    n_total = len(comparisons)
    if not n_total:
        return {}

    per_signal = {sn: {"exact": 0, "neighbour": 0, "n": 0} for sn in SIGNAL_NAMES}
    overall = {"exact": 0, "neighbour": 0}

    for cmp in comparisons:
        haiku_overall = cmp.get("haiku_overall_risk", "")
        sonnet_overall = cmp.get("sonnet_overall_risk", "")
        if haiku_overall == sonnet_overall:
            overall["exact"] += 1
        if overall_risk_neighbour(haiku_overall, sonnet_overall):
            overall["neighbour"] += 1

        for sn in SIGNAL_NAMES:
            haiku_v = cmp.get("haiku_signals", {}).get(sn, {}).get("value", "")
            sonnet_v = cmp.get("sonnet_signals", {}).get(sn, {}).get("value", "")
            if not haiku_v or not sonnet_v:
                continue
            per_signal[sn]["n"] += 1
            if haiku_v == sonnet_v:
                per_signal[sn]["exact"] += 1
            if neighbour_match(sn, haiku_v, sonnet_v):
                per_signal[sn]["neighbour"] += 1

    summary = {
        "n_compared": n_total,
        "overall_risk": {
            "exact_pct":     round(100.0 * overall["exact"] / n_total, 1),
            "neighbour_pct": round(100.0 * overall["neighbour"] / n_total, 1),
        },
        "per_signal": {},
    }
    for sn in SIGNAL_NAMES:
        n = per_signal[sn]["n"]
        if n == 0:
            summary["per_signal"][sn] = {"n": 0, "exact_pct": None, "neighbour_pct": None}
            continue
        summary["per_signal"][sn] = {
            "n":             n,
            "exact_pct":     round(100.0 * per_signal[sn]["exact"] / n, 1),
            "neighbour_pct": round(100.0 * per_signal[sn]["neighbour"] / n, 1),
        }
    return summary


def extract_disagreements(comparisons: list[dict], max_n: int = 10) -> list[dict]:
    out = []
    for cmp in comparisons:
        diffs = []
        # Overall risk diff
        if cmp.get("haiku_overall_risk") != cmp.get("sonnet_overall_risk"):
            diffs.append({
                "signal": "overall_risk",
                "haiku":  cmp.get("haiku_overall_risk"),
                "sonnet": cmp.get("sonnet_overall_risk"),
            })
        for sn in SIGNAL_NAMES:
            hv = cmp.get("haiku_signals", {}).get(sn, {}).get("value")
            sv = cmp.get("sonnet_signals", {}).get(sn, {}).get("value")
            if hv and sv and hv != sv:
                diffs.append({
                    "signal": sn,
                    "haiku":  hv,
                    "sonnet": sv,
                    "sonnet_evidence": cmp.get("sonnet_signals", {})
                                          .get(sn, {}).get("evidence", "")[:240],
                })
        if diffs:
            out.append({
                "server_id":   cmp.get("server_id"),
                "mcp_name":    cmp.get("mcp_name"),
                "description": (cmp.get("description") or "")[:200],
                "diffs":       diffs,
            })
        if len(out) >= max_n:
            break
    return out


def main() -> int:
    started_iso = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    if not acquire_lock():
        write_probe_output({
            "probe": "signal_spot_check", "version": VERSION,
            "started_at": started_iso,
            "verdict": "skipped_locked",
            "hostname": socket.gethostname(),
        })
        return 0

    try:
        api_key = os.environ.get(KEY_ENV_VAR, "")
        if not api_key:
            write_probe_output({
                "probe": "signal_spot_check", "version": VERSION,
                "started_at": started_iso,
                "verdict": "missing_api_key",
                "hostname": socket.gethostname(),
            })
            return 0

        print("=== signal_spot_check v1.0 ===")
        print("sampling stratified 50 MCPs...")
        sample = sample_stratified()
        if not sample:
            write_probe_output({
                "probe": "signal_spot_check", "version": VERSION,
                "started_at": started_iso,
                "verdict": "empty_sample",
                "hostname": socket.gethostname(),
            })
            return 0
        print(f"sampled {len(sample)} MCPs")

        comparisons = []
        errors = []
        for i, mcp in enumerate(sample):
            print(f"  [{i+1}/{len(sample)}] {mcp['mcp_name'][:50]} "
                  f"(haiku={mcp['overall_risk']})")
            sonnet_resp, err = call_sonnet(api_key, mcp)
            if not sonnet_resp:
                errors.append({"mcp": mcp["mcp_name"], "error": err})
                continue
            comparisons.append({
                "server_id":           mcp["server_id"],
                "mcp_name":            mcp["mcp_name"],
                "description":         mcp.get("description", ""),
                "haiku_overall_risk":  mcp["overall_risk"],
                "sonnet_overall_risk": sonnet_resp.get("overall_risk", ""),
                "haiku_signals":       mcp["haiku_signals"],
                "sonnet_signals":      sonnet_resp.get("signals", {}),
            })

        agreement = compute_agreement(comparisons)
        disagreements = extract_disagreements(comparisons, max_n=10)

        out_path = write_probe_output({
            "probe":            "signal_spot_check",
            "version":          VERSION,
            "started_at":       started_iso,
            "finished_at":      datetime.now(timezone.utc).isoformat(),
            "duration_s":       round(time.time() - t0, 1),
            "verdict":          "ok" if comparisons else "all_failed",
            "validation_model": VALIDATION_MODEL,
            "teacher_model":    TEACHER_MODEL,
            "sample_plan":      SAMPLE_PLAN,
            "n_attempted":      len(sample),
            "n_compared":       len(comparisons),
            "n_errors":         len(errors),
            "agreement":        agreement,
            "disagreements":    disagreements,
            "errors":           errors[:10],
            "hostname":         socket.gethostname(),
        })
        print(f"\nspot-check -> {out_path}")
        print(f"agreement overall_risk: {agreement.get('overall_risk', {})}")
        return 0
    except Exception as e:
        import traceback
        write_probe_output({
            "probe": "signal_spot_check", "version": VERSION,
            "started_at": started_iso,
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