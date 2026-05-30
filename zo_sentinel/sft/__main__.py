"""
CLI for the SFT ingestion component.

    python -m zo_sentinel.sft status                 # queue snapshot + dormancy
    python -m zo_sentinel.sft validate job.json      # validate a spec, no write
    python -m zo_sentinel.sft submit  job.json       # validate + enqueue
    python -m zo_sentinel.sft list   [status]        # list jobs (optionally filtered)

Queue dir: --queue, else $SFT_QUEUE_DIR, else ./sft_queue.
This CLI never launches a GPU job; activation of the batch runner is a separate,
deliberate step (see batch_runner.BatchRunner.is_enabled).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from zo_sentinel.sft.batch_runner import queue_status_report
from zo_sentinel.sft.ingest import IngestQueue
from zo_sentinel.sft.schema import JobSpec, JobStatus, validate_job


def _queue_dir(args) -> Path:
    return Path(args.queue or os.environ.get("SFT_QUEUE_DIR", "sft_queue"))


def _load_spec(path: str) -> JobSpec:
    return JobSpec.from_json(Path(path).read_text(encoding="utf-8"))


def cmd_status(args) -> int:
    print(json.dumps(queue_status_report(_queue_dir(args)), indent=2))
    return 0


def cmd_validate(args) -> int:
    spec = _load_spec(args.spec)
    res = validate_job(spec)
    print(json.dumps({"ok": res.ok, "errors": res.errors, "warnings": res.warnings}, indent=2))
    return 0 if res.ok else 1


def cmd_submit(args) -> int:
    q = IngestQueue(_queue_dir(args))
    spec = _load_spec(args.spec)
    job = q.submit(spec)
    print(f"{job.job_id}: {job.status.value}")
    if job.status == JobStatus.REJECTED:
        print("  reasons:", job.history[-1]["note"], file=sys.stderr)
        return 1
    return 0


def cmd_list(args) -> int:
    q = IngestQueue(_queue_dir(args))
    status = JobStatus(args.status) if args.status else None
    for j in q.list(status):
        print(f"{j.job_id:<20} {j.status.value:<10} prio={j.priority:<4} "
              f"method={j.method} dataset={j.dataset.train_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m zo_sentinel.sft",
                                description="SFT training-job ingestion")
    p.add_argument("--queue", help="queue dir (default $SFT_QUEUE_DIR or ./sft_queue)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    v = sub.add_parser("validate"); v.add_argument("spec"); v.set_defaults(func=cmd_validate)
    s = sub.add_parser("submit"); s.add_argument("spec"); s.set_defaults(func=cmd_submit)
    ls = sub.add_parser("list"); ls.add_argument("status", nargs="?"); ls.set_defaults(func=cmd_list)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
