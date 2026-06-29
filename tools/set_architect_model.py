#!/usr/bin/env python3
r"""
set_architect_model.py -- one-shot host patcher that routes the goose architect
(sentinel_directive_generator_goose.py, go.sh section 12.5b) OFF the weak
MiniMax rung-0 and onto a capable, tool-calling rung.

Why: the goose architect defaults to GOOSE_MODEL=MiniMax-Text-01 (rung 0) -- the
documented '+0/fixation bottleneck'. It over-explores (list_domains -> graph_neighbors
loops) and TIMES OUT at 240s without ever reaching propose_directive. The fix the
code itself recommends: ZO_ARCHITECT_MODEL=zo-ladder-cerebras (Cerebras gpt-oss-120b
-- free, tool-calling, the bake-off winner, with free-rung failover).

Host edit (go.sh 12.5b): insert `export ZO_ARCHITECT_MODEL=...` immediately before
the goose-architect launch so the daemon inherits it. Additive one line -- NOT a new
daemon, so no boot-herd. The 12.5 legacy block + everything else are untouched.
Reversible: remove the export.

Usage (on ZoComputer):
    python3 set_architect_model.py --dry-run                 # report only
    python3 set_architect_model.py                           # patch go.sh (.bak)
    python3 set_architect_model.py --model zo-ladder-nvidia  # a different rung
Applies on the next `zm go`. For IMMEDIATE effect without a full zm go, the script
prints the exact one-line relaunch command (precise pkill of the _goose process +
relaunch with the env) for you to run.

Idempotent + safe: skipped if already set; if the 12.5b anchor is not found verbatim
(version drift) it is skipped with a warning and nothing else is touched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

GO_DEFAULT = "/home/workspace/zo_mesh/go.sh"

ANCHOR = (
    'hdr "12.5b Sentinel Directive Generator (Goose -- Phase 0b sibling)"\n'
    "nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator_goose $SENTINEL/sentinel_directive_generator_goose.py >> $LOGS/sentinel_directive_generator_goose.log 2>&1 &\n"
)
SEEN = "export ZO_ARCHITECT_MODEL="

RELAUNCH = (
    "pkill -f 'sentinel_directive_generator_goose\\.py' ; sleep 2 ; "
    "ZO_ARCHITECT_MODEL={model} nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh "
    "sentinel_directive_generator_goose /home/workspace/zo_sentinel/sentinel_directive_generator_goose.py "
    ">> /home/workspace/logs/sentinel_directive_generator_goose.log 2>&1 &"
)


def _patch_go(path: Path, model: str, dry: bool) -> str:
    src = path.read_text(encoding="utf-8")
    if SEEN in src:
        return "go.sh: ZO_ARCHITECT_MODEL already set (skip) -- edit/remove by hand to change"
    if ANCHOR not in src:
        return "go.sh: section-12.5b anchor NOT found verbatim -- skipped (version drift?)"
    new = (
        'hdr "12.5b Sentinel Directive Generator (Goose -- Phase 0b sibling)"\n'
        f'export ZO_ARCHITECT_MODEL="{model}"   # capable tool-calling rung -- fixes the MiniMax rung-0 +0/tool-loop. Unset to revert.\n'
        "nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator_goose $SENTINEL/sentinel_directive_generator_goose.py >> $LOGS/sentinel_directive_generator_goose.log 2>&1 &\n"
    )
    out = src.replace(ANCHOR, new, 1)
    if dry:
        return f"go.sh: [dry-run] would insert export ZO_ARCHITECT_MODEL={model} before the 12.5b launch"
    path.with_suffix(path.suffix + ".bak").write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    return f"go.sh: inserted export ZO_ARCHITECT_MODEL={model} (backup: {path.name}.bak)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", default=GO_DEFAULT)
    ap.add_argument("--model", default="zo-ladder-cerebras")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.go)
    if not p.exists():
        print(f"  WARN: go.sh not found at {p} -- skipped")
        return 2
    print("  " + _patch_go(p, args.model, args.dry_run))
    if not args.dry_run:
        print("Applies on the next `zm go`. For IMMEDIATE effect, run this one line:")
        print("    " + RELAUNCH.format(model=args.model))
        print("Then watch: tail -f /home/workspace/logs/sentinel_directive_generator_goose.log")
        print("Expect 'invoking goose ... model=zo-ladder-cerebras' then a real propose (not +0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
