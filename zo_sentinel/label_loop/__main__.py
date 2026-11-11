"""
CLI for the label loop. Design: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §4.

    python -m zo_sentinel.label_loop status
    python -m zo_sentinel.label_loop tick --registry-count N [--dry-run]
    python -m zo_sentinel.label_loop gate --scores eval_scores.json

`tick` without --registry-count reads the live count through the bus
(127.0.0.1:8772 -- the only allowed data path); with it, the tick is fully
offline. The teacher adapter is lane-owned and NOT wired here: a tick from
this CLI uses a refusing placeholder teacher, so the corpus can never be
grown from an unreviewed prompt by accident -- the CLI exists for status,
dry-run rehearsal, and gate evaluation.

`gate` exits 1 on RED so the lane's alert path can consume it directly.
scores JSON: {"scores": [...], "trusted_scores": [...], "threat_scores": [...],
"null_slice_abstained": 0, "null_slice_total": 0, "total_abstained": 0}
(only "scores" is required; omitted slices are reported as NOT RUN).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_STATE = REPO_ROOT / "zo_sentinel" / "label_loop" / "state" / "label_loop_state.json"
DEFAULT_CORPUS = REPO_ROOT / "zo_sentinel" / "label_loop" / "corpus_v4"
DEFAULT_QUEUE = REPO_ROOT / "zo_sentinel" / "sft" / "queue"


def _live_registry_count() -> int:
    import requests  # local import: status/gate paths stay network-free
    resp = requests.post("http://127.0.0.1:8772/query",
                         json={"sql": "SELECT COUNT(*) AS n FROM mcp_server_registry"},
                         timeout=10)
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    return int(rows[0].get("n", 0)) if rows else 0


def _refusing_teacher(servers):
    raise RuntimeError(
        "no teacher wired in the CLI -- the teacher adapter is lane-owned "
        "(design S4 step 2). Use run_once() with an injected teacher_fn.")


def cmd_status(args) -> int:
    from zo_sentinel.label_loop.corpus import CorpusStore
    from zo_sentinel.label_loop.trigger import RowTrigger
    from zo_sentinel.sft.batch_runner import queue_status_report
    from zo_sentinel.sft.dispatcher import ARM_FLAG, is_armed

    trigger = RowTrigger(args.state)
    corpus = CorpusStore(args.corpus)
    entries = corpus.manifest_entries()
    print(json.dumps({
        "armed": is_armed(), "arm_flag": ARM_FLAG,
        "watermark": trigger.watermark,
        "corpus_rows": corpus.total_rows(),
        "manifest_decisions": {
            d: sum(1 for e in entries if e.get("decision") == d)
            for d in ("appended", "quarantined", "failed_out")},
        "queue": queue_status_report(args.queue),
    }, indent=2))
    return 0


def cmd_tick(args) -> int:
    from zo_sentinel.label_loop.loop import run_once

    count = args.registry_count if args.registry_count is not None else _live_registry_count()
    report = run_once(
        registry_count=count,
        fetch_fn=lambda b: [],          # CLI rehearsal: no row fetch wired
        teacher_fn=(lambda s: []) if args.dry_run else _refusing_teacher,
        state_path=args.state, corpus_dir=args.corpus, queue_dir=args.queue,
        dry_run=args.dry_run,
    )
    print(json.dumps(report.__dict__, indent=2, default=str))
    return 0


def cmd_gate(args) -> int:
    from zo_sentinel.label_loop.gate import evaluate_scores

    data = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    res = evaluate_scores(
        data.get("scores", []),
        trusted_scores=data.get("trusted_scores"),
        threat_scores=data.get("threat_scores"),
        null_slice_abstained=data.get("null_slice_abstained"),
        null_slice_total=data.get("null_slice_total"),
        total_abstained=data.get("total_abstained"),
    )
    print(json.dumps({"green": res.green, "checks": res.checks,
                      "numbers": res.numbers, "reasons": res.reasons}, indent=2))
    return 0 if res.green else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="zo_sentinel.label_loop")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="watermark + corpus + queue + arm state")
    s.set_defaults(fn=cmd_status)

    t = sub.add_parser("tick", help="one loop tick (default --dry-run rehearsal)")
    t.add_argument("--registry-count", type=int, default=None)
    t.add_argument("--dry-run", action="store_true", default=False)
    t.set_defaults(fn=cmd_tick)

    g = sub.add_parser("gate", help="evaluate the discrimination gate; rc 1 on RED")
    g.add_argument("--scores", required=True)
    g.set_defaults(fn=cmd_gate)

    for sp in (s, t):
        sp.add_argument("--state", default=str(DEFAULT_STATE))
        sp.add_argument("--corpus", default=str(DEFAULT_CORPUS))
        sp.add_argument("--queue", default=str(DEFAULT_QUEUE))

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
