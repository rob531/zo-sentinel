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
  4. TIMEOUT **every** full_schema_bootstrap.py call. go.sh runs it in TWO
     branches (writer-healthy vs writer-waited); an un-bounded foreground
     full_schema_bootstrap.py wedges the boot if write_service answers 200 then
     hangs mid-write. Measured 2026-10-01: a bare second call wedged `zm go` in
     state S for ~2.4h -- the herd had launched but the launcher never completed,
     so 5 daemons were never re-parented under their wrappers. The earlier version
     of this patcher hardened only the FIRST call (str.replace(.., 1) + an
     "already present" skip once one was timed), which is exactly how the second
     call stayed bare. #3 and #4 now patch EVERY occurrence, idempotently.

Usage (on ZoComputer):
    python3 tools/harden_go_sh.py            # patch in place (.hardbak written)
    python3 tools/harden_go_sh.py --dry-run  # report only
    python3 tools/harden_go_sh.py --self-test
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

# --- 2: readiness gate (inserted right before section 4, one-time) -----------
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

# --- 3 & 4: prefix-add timeouts, applied to EVERY occurrence. Idempotent via a
# fixed-width negative lookbehind, so an already-timed call is never re-prefixed
# and re-runs are no-ops. (Previously these were str.replace(old, new, 1) guarded
# by `if seen in out: skip`, so once ONE call was timed the patcher skipped the
# rest -- leaving go.sh's second full_schema_bootstrap.py bare. See module docs.)
PREFIX_PATCHES = [
    ("git clone timeout",
     re.compile(r"(?<!timeout 60 )git clone https://github\.com/rob531/zo-sentinel"),
     "timeout 60 git clone https://github.com/rob531/zo-sentinel"),
    ("bootstrap timeout",
     re.compile(r"(?<!timeout 120 )python3 \$SENTINEL/full_schema_bootstrap\.py 2>&1"),
     "timeout 120 python3 $SENTINEL/full_schema_bootstrap.py 2>&1"),
]

# match a bare `curl -s` (word boundary so `curl -m5 -s`/`curl -m3 -s` -- which
# do not contain the substring "curl -s" -- are NOT matched: idempotent)
_BARE_CURL = re.compile(r"curl -s\b")


def harden_text(src: str):
    """Return (out, applied, skipped). Pure -- used by main() and self_test()."""
    out, applied, skipped = src, [], []

    # 1: curl timeouts (regex, idempotent) -- run FIRST so the readiness gate's
    # own `curl -m3 -s` (inserted below, never a bare `curl -s`) is never touched.
    out, n = _BARE_CURL.subn("curl -m5 -s", out)
    if n:
        applied.append(f"curl -m5 (x{n})")
    else:
        skipped.append("curl timeouts (none bare -- already hardened)")

    # 2: readiness gate (one-time block insertion)
    if SEEN_GATE in out:
        skipped.append("3b readiness gate (already present)")
    elif OLD_GATE_ANCHOR in out:
        out = out.replace(OLD_GATE_ANCHOR, NEW_GATE, 1)
        applied.append("3b readiness gate")
    else:
        skipped.append("3b readiness gate (anchor NOT found -- version drift?)")

    # 3-4: prefix timeouts on EVERY un-timed occurrence (idempotent)
    for name, rx, repl in PREFIX_PATCHES:
        out, k = rx.subn(repl, out)
        if k:
            applied.append(f"{name} (x{k})")
        else:
            skipped.append(f"{name} (none unpatched -- already hardened or anchor drift)")

    return out, applied, skipped


def self_test() -> int:
    # Fixture with the exact shape that bit us: TWO full_schema_bootstrap.py calls
    # (an if/else), a bare curl, the gate anchor, and a clone.
    fix = (
        'hdr "3. WriteService"\n'
        'curl -s -o /dev/null http://127.0.0.1:8772/health\n'
        'hdr "4. InferenceRouter :8773"\n'
        'timeout 0 git clone https://github.com/rob531/zo-sentinel-x /tmp/x\n'  # non-matching
        'git clone https://github.com/rob531/zo-sentinel /tmp/y\n'
        'if [[ ok ]]; then\n'
        '    python3 $SENTINEL/full_schema_bootstrap.py 2>&1 | tee a.log\n'
        'else\n'
        '    python3 $SENTINEL/full_schema_bootstrap.py 2>&1 | tee b.log\n'
        'fi\n'
    )
    checks = []
    out, applied, _ = harden_text(fix)
    nboot = out.count("timeout 120 python3 $SENTINEL/full_schema_bootstrap.py 2>&1")
    # RED (the fault this fix closes): the old count=1/skip logic left 1 bare.
    checks.append(("BOTH bootstrap calls hardened (the bug was: only 1)", nboot == 2))
    checks.append(("no bare bootstrap remains",
                   "\n    python3 $SENTINEL/full_schema_bootstrap.py" not in out))
    checks.append(("bare curl got -m5", "curl -m5 -s -o" in out))
    checks.append(("git clone timed out", "timeout 60 git clone https://github.com/rob531/zo-sentinel " in out))
    checks.append(("readiness gate inserted", SEEN_GATE in out))
    # GREEN pole: idempotent re-run is a no-op, with no doubled prefixes.
    out2, _, _ = harden_text(out)
    checks.append(("idempotent re-run (no change)", out2 == out))
    checks.append(("no doubled timeout prefix",
                   "timeout 120 timeout 120" not in out2 and "timeout 60 timeout 60" not in out2))
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    npass = sum(o for _, o in checks)
    print(f"harden_go_sh self-test: {npass}/{len(checks)}")
    return 0 if npass == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    src = path.read_text(encoding="utf-8")
    out, applied, skipped = harden_text(src)

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
    print("Re-run `zm go` -- bounded curls, a readiness gate before the herd, and "
          "EVERY full_schema_bootstrap.py call timed out (both branches).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
