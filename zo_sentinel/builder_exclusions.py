"""The builder's family exclusion input -- S7 of the staging_drain chain.

ONE writer: `tools/staging_drain/exclusions.py` derives `directives/builder_exclusions.json`
from the chain ledger (promoted + superseded staged families, plus every family that
is already live under services/active). Everything else READS it through this module:

  * zo_sentinel/promoters/proposed_to_pending_promoter.py -- a `build_service`
    proposal for an excluded family is rejected (`.excluded`) instead of being
    fanned out into five scaffold directives;
  * zo_sentinel/sentinel_directive_generator_goose.py -- the starvation floor adds
    the excluded families to its candidate exclude set.

Why: staging grew from ~426 to 1,776 directories because the builder kept emitting
new permutations of families that were already live or already superseded
(`the regeneration is how staging grew to 426`). The exclusion file is the
structural kill for that class (LOCO_CHAIRMAN GC-12): once a family is live, the
emitter is told so in a file it reads, not in prose it does not.

Stdlib only. Never raises on a missing or malformed file -- it returns an empty
exclusion set AND says so via `load().get("status")`, so a reader can tell
"nothing excluded" from "file unreadable" (R6: unknown is not zero).
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "directives", "builder_exclusions.json")
_VERSION_RE = re.compile(r"^(?P<family>.+?)_v(?P<ver>\d+)$")
_SERVICE_PREFIXES = ("build_service_", "scaffold_", "build_")


def family_of(name: str) -> str:
    """`foo_v3` -> `foo`; `foo` -> `foo`. The same rule the census uses."""
    m = _VERSION_RE.match(name or "")
    return m.group("family") if m else (name or "")


def load(path: Optional[str] = None) -> Dict:
    """Return {"status": "ok"|"missing"|"unreadable", "families": {family: reason}}."""
    p = path or os.environ.get("ZO_BUILDER_EXCLUSIONS", DEFAULT_PATH)
    if not os.path.isfile(p):
        return {"status": "missing", "path": p, "families": {}}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        fams = data.get("families") or {}
        if not isinstance(fams, dict):
            raise ValueError("families is not an object")
        return {"status": "ok", "path": p,
                "families": {str(k): (v if isinstance(v, dict) else {"reason": str(v)})
                             for k, v in fams.items()},
                "generated_at": data.get("generated_at"), "writer": data.get("writer")}
    except (OSError, ValueError) as exc:
        return {"status": "unreadable", "path": p, "families": {}, "detail": str(exc)}


def excluded_reason(service_name: str, path: Optional[str] = None) -> Optional[str]:
    """The reason `service_name` (or its family) is excluded, else None.

    Accepts a bare service name, a `_vN` variant, a `.py` basename, or a task
    name carrying a build_/scaffold_ prefix -- the shapes the emitters hold."""
    raw = str(service_name or "").strip()
    if raw.endswith(".py"):
        raw = raw[:-3]
    for pfx in _SERVICE_PREFIXES:
        if raw.startswith(pfx):
            raw = raw[len(pfx):]
            break
    fam = family_of(raw.strip("_"))
    if not fam:
        return None
    entry = load(path)["families"].get(fam)
    if entry is None:
        return None
    return str(entry.get("reason") or "excluded")


def excluded_names(path: Optional[str] = None) -> set:
    """Every excluded family plus its `.py` spelling -- the architect's exclude-set shape."""
    fams = set(load(path)["families"])
    return fams | {f + ".py" for f in fams}
