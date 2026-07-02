#!/usr/bin/env python3
"""directive_simplifier -- "decompose, don't muscle".

A durably-quarantined HARD build directive (one that repeatedly ghosted or shipped a
hollow stub) is usually too big for a single-file builder pass. Rather than muscle it
through a stronger model, DECOMPOSE it into 2-3 SIMPLE, single-responsibility
sub-directives the bulk builder (MiniMax) can reliably build.

Decomposition is a TEXT task (no tool_calls) -> plays to MiniMax's strength and runs
on the cheap/unmetered rung. Sub-directives are written to directives/proposed/ (the
safe entry the architect uses); the normal promoter + goose_runner flow then builds
them. The parent quarantine sentinel is archived so it is not re-processed.

SAFE BY DEFAULT: --dry-run prints the plan and writes nothing. --apply performs the
writes. --limit caps parents processed per run (cost ceiling). NOT wired into go.sh --
run manually until proven.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import pathlib
from datetime import datetime, timezone

SENTINEL_DIR = pathlib.Path("/home/workspace/zo_sentinel")
DIRECTIVES = SENTINEL_DIR / "directives"
PROPOSED_DIR = DIRECTIVES / "proposed"
QUARANTINE = pathlib.Path("/home/workspace/zo_sentinel_state/quarantine")
MAX_SUBS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_original_directive(task_name):
    """Find the original directive JSON for a quarantined task
    (pending/done/proposed, then retired/).

    retired/ is searched LAST and recursively: the queue_janitor retires
    quarantined squatters out of pending/ to directives/retired/<utc>/<class>/
    (the skip=>retire fix), so a quarantined parent's directive JSON may live
    there by the time the simplifier decomposes it. Newest retirement batch
    wins (reverse-sorted timestamp dirs)."""
    for sub in ("pending", "done", "proposed"):
        d = DIRECTIVES / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            if task_name in p.name:
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
    retired = DIRECTIVES / "retired"
    if retired.is_dir():
        for p in sorted(retired.rglob("*.json"), reverse=True):
            if task_name in p.name:
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
    return None


def _decompose_prompt(directive: dict) -> str:
    task = directive.get("task", "unknown")
    desc = directive.get("description", "")
    out = directive.get("output_file", str(task) + ".py")
    return (
        "You are decomposing a software build directive that a single-file code builder "
        "REPEATEDLY FAILED to build (it ghosted or shipped a hollow stub). Do NOT try to "
        "build it. Break it into 2-3 SIMPLE, INDEPENDENT sub-tasks, each a single "
        "responsibility a modest model can build in one file.\n\n"
        "PARENT TASK: " + str(task) + "\n"
        "PARENT OUTPUT: " + str(out) + "\n"
        "PARENT DESCRIPTION: " + str(desc) + "\n\n"
        "Rules for sub-tasks:\n"
        "- Each must be independently buildable and testable (its own __main__ self-test).\n"
        "- Each keeps the SAME data-access contract as the parent: read via the app.db "
        "session / write_service; never invent an in-memory data layer.\n"
        "- Prefer splitting by concern: (a) the DB read/query layer, (b) response "
        "shaping/serialization, (c) the FastAPI route wiring.\n"
        "- complexity MUST be low.\n\n"
        "Return ONLY a strict JSON array, no prose:\n"
        "[{\"task\": \"<snake_case_id>\", \"output_file\": \"<file.py>\", "
        "\"description\": \"<one concrete paragraph>\"}]"
    )


def _extract_json_array(text: str):
    """Pull the first top-level JSON array of objects out of an LLM response."""
    if not text:
        return None
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, list) else None
    except Exception:
        return None


def _build_subdirectives(raw_list, parent_task: str):
    subs = []
    for item in (raw_list or [])[:MAX_SUBS]:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip()
        desc = str(item.get("description", "")).strip()
        if not task or not desc:
            continue
        out = str(item.get("output_file", "") or (task + ".py")).strip()
        if not (out.endswith(".py") or out.endswith(".html")):
            out += ".py"
        subs.append({
            "task": task,
            "handler": "generate_file",
            "output_file": out,
            "complexity": "low",
            "description": desc,
            "proposed_at": _now(),
            "source": "directive_simplifier",
            "parent_task": parent_task,
        })
    return subs


def _write_proposed(sub: dict) -> pathlib.Path:
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    fn = PROPOSED_DIR / ("simplified_" + sub["task"] + ".json")
    fn.write_text(json.dumps(sub, indent=2), encoding="utf-8")
    return fn


def _archive_parent(sentinel: pathlib.Path) -> None:
    dest = QUARANTINE / ".simplified"
    dest.mkdir(parents=True, exist_ok=True)
    sentinel.rename(dest / sentinel.name)


def _call_ladder(prompt: str) -> str:
    """Decompose via the ladder (text-only). Lazy import so the module + tests do not
    require the escalation stack."""
    if str(SENTINEL_DIR) not in sys.path:
        sys.path.insert(0, str(SENTINEL_DIR))
    from escalation import ask  # type: ignore
    res = ask("default", prompt, max_tokens=1500, temperature=0.3)
    return getattr(res, "text", "") or ""


def simplify_one(sentinel: pathlib.Path, apply: bool) -> dict:
    task = sentinel.name.replace(".failed.json", "")
    directive = _load_original_directive(task) or {
        "task": task, "description": "", "output_file": task + ".py"}
    text = _call_ladder(_decompose_prompt(directive))
    subs = _build_subdirectives(_extract_json_array(text), task)
    result = {"parent": task, "n_subs": len(subs),
              "subs": [s["task"] for s in subs], "applied": False}
    if not subs:
        result["error"] = "no valid sub-directives parsed"
        return result
    if apply:
        for s in subs:
            _write_proposed(s)
        _archive_parent(sentinel)
        result["applied"] = True
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Decompose quarantined hard directives into buildable sub-directives.")
    ap.add_argument("--apply", action="store_true",
                    help="write proposed/ sub-directives + archive parent (default: dry-run)")
    ap.add_argument("--limit", type=int, default=1,
                    help="max quarantined parents to process (cost ceiling)")
    ap.add_argument("--task", help="process a single named quarantined task instead of scanning")
    args = ap.parse_args(argv)

    if args.task:
        sentinels = [QUARANTINE / (args.task + ".failed.json")]
    else:
        sentinels = sorted(QUARANTINE.glob("*.failed.json"))[:max(0, args.limit)]
    if not sentinels:
        print("no quarantined directives to simplify")
        return 0
    for s in sentinels:
        if not s.is_file():
            print(json.dumps({"parent": s.stem, "error": "sentinel not found"}))
            continue
        print(json.dumps(simplify_one(s, apply=args.apply)))
    if not args.apply:
        print("DRY-RUN: nothing written. re-run with --apply to emit proposed/ sub-directives.")
    return 0


if __name__ == "__main__":
    sys.exit(main())