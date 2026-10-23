#!/usr/bin/env python3
"""
shadow_corpus_capture.py -- REPORT-ONLY, fail-graceful capture of build tuples.

WHY
---
We want a "shadow corpus" of (directive -> prompt -> response -> code -> CI)
build tuples to later train a local model on how the mesh actually builds. The
two signals we can capture cheaply at the seams:

  * Hook 1 (prompt/response): every builder model call flows through the ladder
    shim's /v1/chat/completions (ladder_shim.py, port 8796) -> escalation.ask().
    That is the single choke-point for model I/O.
  * Hook 2 (PR<->build join): when the producer successfully opens a PR
    (publisher.ProducerCommit), we record the directive<->PR link so the corpus
    row can be joined to its merged code + CI result later.

ABSOLUTE CONSTRAINT (why this module exists as a thin, dependency-free helper)
-------------------------------------------------------------------------------
The builder MUST NOT be blocked, slowed, or broken by capture. Every public
function here:

  * is GATED by the env flag SHADOW_CORPUS_CAPTURE. Flag unset/false => the
    function returns immediately, does NO work, writes NO file, and has ZERO
    observable effect. Capture is OFF by default -> byte-identical behavior.
  * swallows ALL exceptions. It NEVER raises into the caller. A failed append
    (unwritable path, disk full, bad data) is logged to a side failure log on a
    best-effort basis and otherwise ignored.
  * does a plain best-effort append of ONE JSONL line. No network, no DB, no
    write_service round-trip (fewer failure modes than TrainingCorpusWriter,
    same fail-graceful contract: best-effort + failure-log for later recovery).

This mirrors signal_training_corpus.TrainingCorpusWriter's proven pattern
(best-effort write, failure-log on disk, never raise) but targets flat JSONL.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# --- flag -------------------------------------------------------------------
# The single on/off switch. Default OFF -> the module is completely inert.
CAPTURE_FLAG = "SHADOW_CORPUS_CAPTURE"

# --- output paths (best-effort; dirs created lazily, failures swallowed) ----
LOG_DIR = "/home/workspace/logs"
PROMPT_RESPONSE_LOG = LOG_DIR + "/goose_prompt_response.jsonl"
PR_LEDGER_LOG = LOG_DIR + "/pr_publisher_ledger.jsonl"
FAILURE_LOG = LOG_DIR + "/shadow_corpus_capture_failures.log"

_TRUTHY = ("1", "true", "yes", "on")


def capture_enabled() -> bool:
    """True only when SHADOW_CORPUS_CAPTURE is explicitly truthy. Never raises."""
    try:
        return os.environ.get(CAPTURE_FLAG, "").strip().lower() in _TRUTHY
    except Exception:
        return False


def _iso_now() -> str:
    try:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except Exception:
        return ""


def _log_failure(where: str, err: object) -> None:
    """Best-effort side-channel note for later recovery. Never raises."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(FAILURE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"ts": _iso_now(), "where": where, "error": repr(err)[:500]},
                default=str) + "\n")
    except Exception:
        # Even the failure log is best-effort. If THIS fails there is nothing
        # safe left to do -- stay silent rather than risk the caller.
        pass


def _append_jsonl(path: str, record: dict, where: str) -> bool:
    """Append one JSONL line, best-effort. Returns True on success, False on any
    failure (never raises). Creates the log dir if missing."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(record, default=str, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception as e:
        _log_failure(where, e)
        return False


# --- Hook 1: prompt/response at the inference router ------------------------
def capture_prompt_response(
    *,
    model=None,
    prompt=None,
    response=None,
    finish_reason=None,
    prompt_tokens=None,
    response_tokens=None,
    directive_id=None,
    task=None,
    tier=None,
) -> bool:
    """Append one prompt/response tuple to goose_prompt_response.jsonl.

    Flag-gated + fully swallowed. Any missing field is logged as null -- the
    caller never has to compute or supply anything it does not already have.

    directive_id/task/tier are sourced from the build env (ZO_BUILD_TASK /
    ZO_BUILD_TIER) when the caller does not pass them; still logged as null if
    neither is available (the shim is a long-lived process and may not carry the
    per-build env), per the never-fail contract.
    """
    if not capture_enabled():
        return False
    try:
        if directive_id is None:
            directive_id = os.environ.get("ZO_BUILD_TASK") or None
        if task is None:
            task = os.environ.get("ZO_BUILD_TASK") or None
        if tier is None:
            tier = os.environ.get("ZO_BUILD_TIER") or None
        record = {
            "ts": _iso_now(),
            "directive_id": directive_id,
            "task": task,
            "tier": tier,
            "model": model,
            "prompt": prompt,
            "response": response,
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "finish_reason": finish_reason,
        }
        return _append_jsonl(PROMPT_RESPONSE_LOG, record, "capture_prompt_response")
    except Exception as e:
        # Belt-and-braces: even assembling the record must not escape.
        _log_failure("capture_prompt_response", e)
        return False


# --- Hook 2: PR <-> build join ledger ---------------------------------------
def capture_pr_publish(
    *,
    build_id=None,
    directive_id=None,
    pr_number=None,
    pr_url=None,
    branch=None,
    file=None,
    built_at=None,
    task=None,
    tier=None,
    model=None,
) -> bool:
    """Append one directive<->PR join row to pr_publisher_ledger.jsonl.

    Flag-gated + fully swallowed. Makes the directive->PR link machine-queryable
    (today it lives only in the auto/build/<slug> branch name). Any field the
    publisher cannot supply is logged as null.
    """
    if not capture_enabled():
        return False
    try:
        record = {
            "ts": _iso_now(),
            "build_id": build_id,
            "directive_id": directive_id,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "branch": branch,
            "file": file,
            "built_at": built_at,
            "task": task,
            "tier": tier,
            "model": model,
        }
        return _append_jsonl(PR_LEDGER_LOG, record, "capture_pr_publish")
    except Exception as e:
        _log_failure("capture_pr_publish", e)
        return False


def pr_number_from_url(pr_url):
    """Best-effort extract the trailing PR number from a GitHub PR URL. Returns
    an int, or None on anything unexpected. Never raises."""
    try:
        if not pr_url:
            return None
        tail = str(pr_url).rstrip("/").rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else None
    except Exception:
        return None
