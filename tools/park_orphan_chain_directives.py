#!/usr/bin/env python3
"""park_orphan_chain_directives.py -- keep goose's pending/ queue FLOWING by
parking orphaned-chain directives (and un-parking ones that became buildable).

THE JAM THIS CLOSES (root-caused 2026-10-29, builder idle ~15h):
goose loads its working set from `directives/pending/` (plus a DB query). Service
builds come as a LINEAR chain `logic.py -> router.py -> {contract.py, service.toml}`
(each `requires` the previous file). When the generator emits the DEPENDENTS without
their chain-ROOT `logic` directive (no producer anywhere in the live queue), those
dependents can NEVER satisfy their `requires` -- they defer forever. Enough of them
fill the small pending/ queue so it never runs "thin", so `topup_quarantine` never
refills it, so NOTHING builds. A silent, total stall.

THE RULE (deterministic, falsifiable -- no model judgement):
  * A pending directive is ORPHANED when one of its `requires` paths is BOTH
    (a) absent on disk AND (b) not the `output_file` of any OTHER live pending
    directive -- i.e. nothing will ever produce it. Park it (reversible).
  * A PARKED directive is REVIVED when every `requires` is now satisfied-on-disk or
    has a producer back in pending/ -- move it back so the chain completes.
A directive whose missing requirement HAS a producer in pending/ is just WAITING,
not orphaned -- it is KEPT (that is the normal chain ordering, not a jam).

LOUD by construction: every park/revive is returned + logged, never silent (a stall
that hides is the exact failure this closes). Fail-soft: callers (goose_runner) wrap
it so it can never break the builder; disable with ZO_ORPHAN_PARK=0.

    python tools/park_orphan_chain_directives.py              # dry-run (report only)
    python tools/park_orphan_chain_directives.py --apply      # park + revive
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent          # /home/workspace/zo_sentinel
PENDING_DIR = _REPO / "directives" / "pending"
PARKED_DIR = _REPO / "directives" / "_parked_orphan_chain"


def _requires(d: dict) -> list[str]:
    r = d.get("requires") or []
    return [str(x) for x in r] if isinstance(r, (list, tuple)) else []


def _output(d: dict) -> str | None:
    o = d.get("output_file")
    return str(o) if o else None


def _load(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        d = json.loads(raw) if raw else None
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _exists(repo: Path, rel: str) -> bool:
    # requires/output_file are repo-relative paths (e.g. services/staged/x/router.py)
    return (repo / rel).exists()


def plan(repo: Path, pending_dir: Path, parked_dir: Path) -> dict:
    """Pure: compute which pending dirs to PARK and which parked dirs to REVIVE.
    Returns {'park': [(name, reason)], 'revive': [(name, reason)]} -- no writes."""
    pend = {}
    for f in sorted(pending_dir.glob("*.json")):
        if f.name.startswith((".bak", ".duplicate")):
            continue
        d = _load(f)
        if d is not None:
            pend[f.name] = d
    # Everything pending/ will produce (so a sibling dependent is WAITING, not orphaned).
    producers = {_output(d) for d in pend.values() if _output(d)}

    park = []
    for name, d in pend.items():
        unsat = [r for r in _requires(d)
                 if not _exists(repo, r) and r not in producers]
        if unsat:
            park.append((name, "unsatisfiable requires (no producer): %s" % unsat))

    revive = []
    if parked_dir.exists():
        for f in sorted(parked_dir.glob("*.json")):
            d = _load(f)
            if d is None:
                continue
            reqs = _requires(d)
            still_blocked = [r for r in reqs
                             if not _exists(repo, r) and r not in producers]
            if not still_blocked:
                revive.append((f.name, "requires now satisfied/producible"))
    return {"park": park, "revive": revive}


def apply_plan(pending_dir: Path, parked_dir: Path, p: dict) -> None:
    parked_dir.mkdir(parents=True, exist_ok=True)
    for name, _ in p["park"]:
        src = pending_dir / name
        if src.exists():
            shutil.move(str(src), str(parked_dir / name))
    for name, _ in p["revive"]:
        src = parked_dir / name
        if src.exists():
            shutil.move(str(src), str(pending_dir / name))


def park_orphan_chains(repo: Path = _REPO, pending_dir: Path = PENDING_DIR,
                       parked_dir: Path = PARKED_DIR, apply: bool = False,
                       log=print) -> dict:
    """Entry point for both the CLI and goose_runner. Returns the plan; applies it
    (moving files) only when apply=True. LOUD: logs every park/revive."""
    p = plan(repo, pending_dir, parked_dir)
    for name, why in p["park"]:
        log("[orphan-park] PARK %s -- %s" % (name, why))
    for name, why in p["revive"]:
        log("[orphan-park] REVIVE %s -- %s" % (name, why))
    if apply:
        apply_plan(pending_dir, parked_dir, p)
    if p["park"] or p["revive"]:
        log("[orphan-park] %s: parked %d, revived %d"
            % ("applied" if apply else "dry-run",
               len(p["park"]), len(p["revive"])))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="move files (default: dry-run)")
    args = ap.parse_args(argv)
    p = park_orphan_chains(apply=args.apply)
    if not args.apply:
        print("[dry-run] would park %d, revive %d. Re-run with --apply."
              % (len(p["park"]), len(p["revive"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
