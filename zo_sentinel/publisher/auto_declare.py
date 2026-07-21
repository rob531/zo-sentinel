"""auto_declare.py -- let an autonomous build PR declare its own orphan router.

WHY THIS EXISTS (2026-07-21, discovered by arming the ratchet)
--------------------------------------------------------------
The CofC ruling armed `tools/reachability_ratchet.py --enforce` with a
declare-or-mount hatch: a PR that adds an unmounted router passes by either
mounting it or naming it in `tools/reachability_deferred.json` with a reason.
The ruling reasoned that this makes the gate SATISFIABLE for a builder that is
structurally incapable of mounting.

Arming it proved that reasoning incomplete. The publisher writes exactly ONE
file per PR -- `plan.file_path`, the artifact -- so an autonomous build PR could
not write the declaration either. The hatch was satisfiable only by a human,
which meant ~15 builder PRs/day would have gone red for a rule none of them
could comply with. That is precisely the deadlock the ruling set out to avoid,
arriving through a side door.

This module closes it. The DECLARATION is written by the publisher -- ordinary
deterministic tower-side code -- not by the builder's goose lane, so the
`module_from_exemplar` lane guard is untouched and the builder gains no new
write scope. The publisher already decides the branch, the commit and the PR
body; adding one line to a data file is the same class of act.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not mount anything, does not touch `app/main.py`, does not create
`app/mounts.toml`, and does not suppress the count. A declared router is STILL
an orphan in `orphan_count` and still shows up in the census -- the declaration
buys the PR headroom against the ratchet and nothing else. What stops is SILENT
growth: every auto-declared module lands as a dated, reviewable line in a
committed file that `deferred_router_ledger_report.py` ages and that the
council's 40-entry cap is measured against.

The honest cost of this design is that the graveyard can still grow, just
visibly and with a paper trail. That is the trade the ruling actually chose.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Tuple

DEFERRED_REL = os.path.join("tools", "reachability_deferred.json")

# Same shapes the ratchet counts, deliberately duplicated rather than imported:
# the publisher runs from its own clone and must not depend on tools/ being
# importable there.
ROUTER_DEF = re.compile(r"APIRouter\s*\(|@router\.(get|post|put|delete|patch)")


def is_router_module(rel_path: str, content: str) -> bool:
    """True for a ROOT-level .py that exposes an HTTP router.

    Root-level only, matching the ratchet's own scan: it counts
    `os.listdir(ROOT)`, so a file under app/ or tests/ is not in scope and must
    not be declared (a spurious declaration would read as STALE on the next run
    and fail the gate -- the opposite of the intent).
    """
    if not rel_path.endswith(".py"):
        return False
    if os.path.dirname(rel_path.replace("\\", "/")):
        return False
    return bool(ROUTER_DEF.search(content or ""))


def is_mounted(clone_dir, stem: str) -> bool:
    """Mirror the ratchet: does anything under app/ reference this stem?"""
    base = os.path.join(str(clone_dir), "app")
    if not os.path.isdir(base):
        return False
    pat = re.compile(r"\b%s\b" % re.escape(stem))
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith((".py", ".toml", ".json", ".txt")):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8",
                          errors="replace") as fh:
                    if pat.search(fh.read()):
                        return True
            except OSError:
                continue
    return False


def _reason(stem: str, task: Optional[str], built_at: Optional[str]) -> str:
    who = task or stem
    when = (built_at or "")[:10]
    return ("auto-declared by the publisher for autonomous build '%s'%s: the "
            "builder cannot mount its own module (module_from_exemplar lane "
            "guard), so this router is unmounted pending the mount-lane review. "
            "Remove this entry when it is mounted or deleted."
            % (who, (" on " + when) if when else ""))


def declare(clone_dir, rel_path: str, content: str, task: Optional[str] = None,
            built_at: Optional[str] = None) -> Tuple[bool, str]:
    """Append `rel_path`'s stem to the deferred file if it is a new orphan.

    Returns (changed, detail). Never raises: a declaration failure must not
    fail the publish -- the ratchet will simply flag the PR, which is a loud,
    correct outcome and strictly better than losing the artifact.
    """
    try:
        if not is_router_module(rel_path, content):
            return False, "not a root-level router module"

        stem = os.path.basename(rel_path)[:-3]
        if is_mounted(clone_dir, stem):
            return False, "already mounted -- no declaration needed"

        path = os.path.join(str(clone_dir), DEFERRED_REL)
        if not os.path.exists(path):
            return False, "no deferred file in this clone (ratchet not armed here)"

        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        deferred = doc.get("deferred")
        if not isinstance(deferred, dict):
            return False, "deferred file has an unexpected shape -- not touching it"
        if stem in deferred:
            return False, "already declared"

        deferred[stem] = _reason(stem, task, built_at)
        doc["deferred"] = dict(sorted(deferred.items()))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        return True, "declared %s" % stem
    except Exception as e:  # noqa: BLE001 -- see docstring: never fail the publish
        return False, "auto-declare skipped: %s" % e
