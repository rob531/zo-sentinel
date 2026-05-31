#!/usr/bin/env python3
"""
patch_go_sh.py -- one-shot host patcher for the LIVE zm-go launcher
(/home/workspace/zo_mesh/go.sh). Applies three edits:

  1. Add the ingestion trio to the section-1 pkill list (clean restart, no dups).
  2. Insert a "12.6b" block that launches the ingestor / governor / publisher
     (gated -- dormant until their latch exists).
  3. Swap the ladder_shim launch to ladder_shim_with_keys.sh so the Gemini key
     hydration survives `zm go` (otherwise the bare relaunch loses it).

Usage (on ZoComputer):
    python3 patch_go_sh.py            # patch in place (.bak written)
    python3 patch_go_sh.py --dry-run  # report only
    python3 patch_go_sh.py --file /path/to/go.sh
Then re-run `zm go` (or just launch the three lines from the 12.6b block).

Idempotent + safe: each edit is skipped if already present; if an anchor isn't
found verbatim (version drift) it is skipped with a warning and nothing else is
touched. go.sh lives in the zo_mesh repo, so this is a host-side edit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/go.sh"

# --- 1: pkill list ----------------------------------------------------------
OLD_KILL = ('            registry_api.py approval_workflow.py \\\n'
            '            "${TRUST_PIPELINE[@]}" \\\n')
NEW_KILL = ('            registry_api.py approval_workflow.py \\\n'
            '            zo_sentinel.ingestor zo_sentinel.publisher \\\n'
            '            "${TRUST_PIPELINE[@]}" \\\n')
SEEN_KILL = "zo_sentinel.ingestor zo_sentinel.publisher"

# --- 2: trio launch block (inserted after the Gate Scheduler block) ---------
OLD_GATE = ("GSC=$(pgrep -f 'gate_scheduler.py' 2>/dev/null | head -1)\n"
            '[[ -n "$GSC" ]] && ok "GateScheduler PID $GSC" || warn "GateScheduler failed"\n')
TRIO_BLOCK = '''
hdr "12.6b Code-Artifact Ingestion Trio (gated -- DORMANT until latched)"
# Consume the build_artifact rows the live goose build emits. SAFE to always run:
# the ingestor + publisher no-op until their latch exists, and the governor only
# flips the ingestor latch once builds prove green AND agree with gate_8. Activate:
#   touch $SENTINEL/.ingestor_enabled      (or let the governor do it)
#   export PR_PUBLISHER_CLONE_DIR=<clone>; touch $SENTINEL/.pr_publisher_enabled
nohup env PYTHONPATH="$SENTINEL" python3 -m zo_sentinel.ingestor run --interval 300 >> $LOGS/artifact_ingestor.log 2>&1 &
sleep 1
ING=$(pgrep -f 'zo_sentinel.ingestor run' 2>/dev/null | head -1)
[[ -n "$ING" ]] && ok "ArtifactIngestor PID $ING (dormant until .ingestor_enabled)" || warn "ArtifactIngestor failed"
nohup env PYTHONPATH="$SENTINEL" bash -c 'while true; do python3 -m zo_sentinel.ingestor govern; sleep 600; done' >> $LOGS/activation_governor.log 2>&1 &
sleep 1
GOV=$(pgrep -f 'zo_sentinel.ingestor govern' 2>/dev/null | head -1)
[[ -n "$GOV" ]] && ok "ActivationGovernor PID $GOV" || warn "ActivationGovernor failed"
nohup env PYTHONPATH="$SENTINEL" bash -c 'while true; do python3 -m zo_sentinel.publisher run-once; sleep 600; done' >> $LOGS/pr_publisher.log 2>&1 &
sleep 1
PUB=$(pgrep -f 'zo_sentinel.publisher run-once' 2>/dev/null | head -1)
[[ -n "$PUB" ]] && ok "PRPublisher PID $PUB (dormant until .pr_publisher_enabled)" || warn "PRPublisher failed"
'''
NEW_GATE = OLD_GATE + TRIO_BLOCK
SEEN_GATE = "12.6b Code-Artifact Ingestion Trio"

# --- 3: keyed ladder_shim launch -------------------------------------------
OLD_SHIM = ("    nohup bash $MESH/daemon_wrapper.sh ladder_shim "
            "$SENTINEL/ladder_shim.py >> $LOGS/ladder_shim.log 2>&1 & sleep 3\n")
NEW_SHIM = ("    nohup bash $SENTINEL/ladder_shim_with_keys.sh "
            ">> $LOGS/ladder_shim.log 2>&1 & sleep 3\n")
SEEN_SHIM = "ladder_shim_with_keys.sh"

PATCHES = [
    ("pkill-list trio", SEEN_KILL, OLD_KILL, NEW_KILL),
    ("12.6b trio launch", SEEN_GATE, OLD_GATE, NEW_GATE),
    ("keyed ladder_shim", SEEN_SHIM, OLD_SHIM, NEW_SHIM),
]


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
    for name, seen, old, new in PATCHES:
        if seen in out:
            skipped.append(f"{name} (already present)")
        elif old in out:
            out = out.replace(old, new, 1)
            applied.append(name)
        else:
            skipped.append(f"{name} (anchor NOT found -- version drift?)")

    for s in skipped:
        print(f"  skip: {s}")
    if not applied:
        print("Nothing to apply (already patched or anchors not found). No change.")
        return 0
    if args.dry_run:
        print(f"[dry-run] would apply: {applied}")
        return 0

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Applied {applied} to {path} (backup: {backup})")
    print("Now run `zm go` (or launch the 12.6b lines) to start the trio dormant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
