#!/usr/bin/env python3
"""
reap_redundant_pending.py -- retire already-built directives from the queue.

A "redundant" pending directive = a build/create directive whose DECLARED OUTPUT
already exists on disk with NO open lesson. goose_runner.is_goose_eligible()
already SKIPS these every cycle (the flag-gated dedup, PR #1060) -- but skipping
does not REMOVE them: they sit in directives/pending/ indefinitely, keep counting
toward the proposed/->pending cap that gates the architect, and force goose to
re-scan+skip them every 60s. This tool RETIRES them (moves pending/<id>.json to
directives/retired/<utc>/) so the queue reflects live buildable work.

It reuses the EXACT redundancy test goose_runner uses
(build_completion.declared_output + build_lessons.open_lessons_for), so what it
retires is precisely what goose skips as a redundant rebuild -- never an
edit-class directive (declared_output -> None), never one with an open lesson
(a genuine rebuild request).

Safe: DRY-RUN by default (no changes; --apply to act). Retired files are MOVED
(reversible) to directives/retired/<utc>/, never deleted. Touches only pending/;
never generation, never write_service, never go.sh. Idempotent.

  python3 tools/reap_redundant_pending.py             # dry-run (list only)
  python3 tools/reap_redundant_pending.py --apply      # retire (move) them
  python3 tools/reap_redundant_pending.py --apply --limit 20
"""
import argparse
import datetime
import json
import pathlib
import shutil
import sys

sys.path.insert(0, "/home/workspace/zo_sentinel")

try:
    from zo_sentinel.build_completion import declared_output
    from zo_sentinel.build_lessons import open_lessons_for
except Exception as e:  # pragma: no cover
    print(f"FATAL: cannot import canonical helpers ({e}); aborting rather than "
          f"guess a divergent redundancy rule.", file=sys.stderr)
    sys.exit(2)

DIRECTIVES = pathlib.Path("/home/workspace/zo_sentinel/directives")
PENDING = DIRECTIVES / "pending"
LESSONS_DIR = pathlib.Path("/home/workspace/zo_sentinel/lessons")


def redundant_output(directive):
    """Return the declared output Path iff it already exists with no open lesson."""
    try:
        out = declared_output(directive)
    except Exception:
        return None
    if out is None:
        return None
    try:
        if out.is_file() and not open_lessons_for(LESSONS_DIR, out.name):
            return out
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually move the files (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number retired (0 = no cap)")
    args = ap.parse_args()

    if not PENDING.is_dir():
        print(f"No pending dir at {PENDING}; nothing to do.")
        return

    all_pending = sorted(PENDING.glob("*.json"))
    found = []
    for f in all_pending:
        try:
            directive = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out = redundant_output(directive)
        if out is not None:
            found.append((f, out))

    print(f"pending/={len(all_pending)}  redundant(built, no open lesson)={len(found)}")
    for f, out in found:
        print(f"  redundant: {f.name}  ->  output {out.name} already exists")

    if not args.apply:
        print(f"\nDRY-RUN -- would retire {len(found)}. Re-run with --apply to move "
              f"them to directives/retired/<utc>/.")
        return

    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = DIRECTIVES / "retired" / ts
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for f, out in found:
        if args.limit and n >= args.limit:
            break
        try:
            shutil.move(str(f), str(dest / f.name))
            n += 1
        except Exception as e:
            print(f"  FAILED {f.name}: {e}")
    print(f"\nAPPLIED -- retired {n} directive(s) to {dest} "
          f"(reversible: move them back to pending/).")


if __name__ == "__main__":
    main()
