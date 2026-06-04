#!/usr/bin/env python3
"""
harden_go_sh.py -- host patcher that makes the LIVE zm-go launcher
(/home/workspace/zo_mesh/go.sh) robust against the hangs + thundering-herd
instability that made `zm go` non-responsive / slow to recover (2026-06-03).

Sibling of patch_go_sh.py (which wires daemon launches); this one only hardens.
Four idempotent, drift-safe hardenings (a missing anchor is skipped with a warn):

  1. TIMEOUT every bare curl. go.sh health-checks a bound-but-hung service with
     `curl -s ...` (no -m), which blocks FOREVER -- the classic write_service-hung
     hang that wedges the whole bootstrap. Adds `-m5` to every bare `curl -s`
     (regex; already-timed `curl -m5 -s` / `curl -m3 -s` are left alone).
  2. WriteService READINESS GATE (new section "3b") right after write_service
     launches, BEFORE the ~40-daemon herd. The herd mostly writes to :8772 (the
     single DuckDB writer); starting it before the writer is ready -> lock
     contention / timeouts / 500s (the recurring instability + slow boots). Wait
     up to 45s for :8772=200 HERE -- the old 60s readiness wait was at section 18,
     after everything had already started.
  3. TIMEOUT the publisher-clone `git clone` (no timeout -> a slow/unreachable
     GitHub blocks the cold-boot bootstrap indefinitely).
  4. TIMEOUT full_schema_bootstrap.py (defensive: a write_service that answers 200
     then hangs mid-write would otherwise stall the boot).

Usage (on ZoComputer):
    python3 tools/harden_go_sh.py            # patch in place (.hardbak written)
    python3 tools/harden_go_sh.py --dry-run  # report only
    python3 tools/harden_go_sh.py --file /path/to/go.sh
Re-run after every REFRESH_MODE=reset (it reverts host patches), alongside
patch_go_sh.py. go.sh lives in the zo_mesh repo, so this is a host-side edit.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/go.sh"

# --- 2: readiness gate (inserted right before section 4) --------------------
OLD_GATE_ANCHOR = 'hdr "4. InferenceRouter :8773"\n'
READINESS = '''hdr "3b. WriteService readiness gate (gate the herd on a ready writer)"
# The ~40 daemons below mostly write to :8772 (the single DuckDB writer). Starting
# them before write_service is healthy piles a thundering herd onto a not-ready
# writer -> DuckDB lock contention, timeouts, the recurring instability + slow
# boots. Wait up to 45s for :8772 HERE, before the herd (the old readiness wait
# lived at section 18 -- after everything had already started hammering it).
WS_GATE=0
for i in $(seq 1 15); do
    if [[ "$(curl -m3 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null)" == "200" ]]; then WS_GATE=1; break; fi
    sleep 3
done
[[ "$WS_GATE" == "1" ]] && ok ":8772 ready -- starting dependent daemons" || warn ":8772 NOT ready after 45s -- daemons may contend"

'''
NEW_GATE = READINESS + OLD_GATE_ANCHOR
SEEN_GATE = "3b. WriteService readiness gate"

# --- 3: git clone timeout ---------------------------------------------------
OLD_CLONE = "git clone https://github.com/rob531/zo-sentinel"
NEW_CLONE = "timeout 60 git clone https://github.com/rob531/zo-sentinel"
SEEN_CLONE = "timeout 60 git clone https://github.com/rob531/zo-sentinel"

# --- 4: schema bootstrap timeout --------------------------------------------
OLD_BOOT = "python3 $SENTINEL/full_schema_bootstrap.py 2>&1"
NEW_BOOT = "timeout 120 python3 $SENTINEL/full_schema_bootstrap.py 2>&1"
SEEN_BOOT = "timeout 120 python3 $SENTINEL/full_schema_bootstrap.py"

EXACT_PATCHES = [
    ("3b readiness gate", SEEN_GATE, OLD_GATE_ANCHOR, NEW_GATE),
    ("git clone timeout", SEEN_CLONE, OLD_CLONE, NEW_CLONE),
    ("bootstrap timeout", SEEN_BOOT, OLD_BOOT, NEW_BOOT),
]

# match a bare `curl -s` (word boundary so `curl -m5 -s`/`curl -m3 -s` -- which
# do not contain the substring "curl -s" -- are NOT matched: idempotent)
_BARE_CURL = re.compile(r"curl -s\b")


def harden_curls(text: str):
    return _BARE_CURL.sub("curl -m5 -s", text), len(_BARE_CURL.findall(text))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    src = path.read_text(encoding="utf-8")

    out, applied, skipped = src, [], []

    # 1: curl timeouts (regex, idempotent) -- run first so the inserted gate's
    # own `curl -m3 -s` (added below) is never touched.
    out, n = harden_curls(out)
    if n:
        applied.append(f"curl -m5 (x{n})")
    else:
        skipped.append("curl timeouts (none bare -- already hardened)")

    # 2-4: exact-string patches
    for name, seen, old, new in EXACT_PATCHES:
        if seen in out:
            skipped.append(f"{name} (already present)")
        elif old in out:
            out = out.replace(old, new, 1)
            applied.append(name)
        else:
            skipped.append(f"{name} (anchor NOT found -- version drift?)")

    for s in skipped:
        print(f"  skip: {s}")
    if out == src:
        print("Nothing to apply (already hardened or anchors not found). No change.")
        return 0
    if args.dry_run:
        print(f"[dry-run] would apply: {applied}")
        return 0

    backup = path.with_suffix(path.suffix + ".hardbak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Applied {applied} to {path} (backup: {backup})")
    print("Re-run `zm go` -- it can no longer hang on a bare curl, and the "
          "readiness gate holds the daemon herd until write_service is up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
