#!/usr/bin/env python3
"""FU -> code-subgraph accessor: the third leg of FU-103's ledger/memory/KL join.

FU-103 set out to tie three stores into one FU-keyed context graph:

  P1  ledger <-> MCP memory   -- explode_followups_to_memory.py  (LIVE)
  P2  FU     <-> graphify KL  -- THIS FILE                       (was missing)
  P3  single-blob accessor                                       (not built)

P1 works. P2 was recorded in the ledger as "BUILT + PROVEN in-session" on
2026-07-25 and its artifact never existed on disk: it was built in a session
scratch directory and evaporated with the session, while its claimed success was
taught into the PROTEAN CHARTER of 13 scheduled tasks as a daily instruction
(`python tools/fu_context.py --fu NNN --anchors a.py,b.py`). For four days every
lane that tried step 6 burned the attempt and fell back to grep. This file is
that accessor, written to a TRACKED path so it cannot evaporate again.

WHAT IT DOES
------------
Given an FU number: read its anchors, resolve them against the graphify KL, and
return the 1-hop code subgraph (callers / imports / references) so an agent
grasps what an FU touches without opening the files.

Anchors come from the artifacts graphify-kl-daily-refresh already maintains --
this file deliberately adds no new source of truth:

  <agents>/graphify/_fu_index.json              FU -> {status, title, anchors[]}
  <agents>/graphify/fu_anchor_drift_last.json   graph_commit + unresolved anchors
  <agents>/FOLLOWUPS.md                         fallback: anchors named in the entry

DEGRADES HONESTLY, AND THAT IS THE POINT
----------------------------------------
The KL itself is NOT on the tower. `graphify-out/` carries only `schema_kl.json`;
the ~95k-node graph lives on ZoComputer behind the :8772 DuckDB bus, which
actively refuses connections from the tower. So the neighbour expansion is
genuinely unavailable in some seats, and the failure mode that matters is a tool
that ERRORS there -- because a step-6 instruction that errors is precisely what
sent 13 lanes back to grep.

Therefore `--dry-run`, and any run where the bus is unreachable, still returns the
FU, its status/title, its resolved anchors and its drift, and exits 0, reporting
`kl: unavailable (<reason>)` rather than pretending. An accessor that cannot reach
the graph is not an accessor that failed; it is a smaller accessor. Exit 2 is
reserved for "cannot evaluate at all" (unknown FU, no anchors from any store) --
a probe that cannot evaluate must not report success.

SCOPE, BEFORE YOU READ `unresolved` AS BREAKAGE
-----------------------------------------------
The KL indexes THIS REPO. Ledger anchors routinely name tower-local files that are
not in it (`sprint_import.py`, `backup_select.py`, ...). Those show as
`unresolved` and are OUT OF SCOPE, not missing -- the same namespace trap that
produced two false reds in the 2026-07-29 link audit. So `unresolved` is split by
WHY: `out_of_scope` (no such basename in the repo tree) vs `in_repo_unindexed`
(the file is here but the graph has no node). Only the second is graph staleness.

Usage:
    python tools/fu/fu_context.py --fu 181
    python tools/fu/fu_context.py --fu 181 --anchors weekly_rescore.py,score_validity.py
    python tools/fu/fu_context.py --fu 181 --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fu_ledger  # noqa: E402

EXIT_OK = 0
EXIT_CANNOT_EVALUATE = 2

DEFAULT_AGENTS_DIR = Path(os.environ.get("ZO_AGENTS_DIR", r"D:\zo\Zocomputer Agents"))
BUS_URL = os.environ.get("ZO_GRAPH_BUS", "http://localhost:8772")
BUS_TIMEOUT_S = float(os.environ.get("ZO_GRAPH_BUS_TIMEOUT", "6"))

# A token in ledger prose that looks like a code anchor. Deliberately narrow:
# over-matching prose turns every entry into a fake subgraph.
#
# BASENAME ONLY, on purpose. The first version matched `[\w./\\-]+\.ext` to keep
# the directory prefix for display, and running it against FU-181 immediately
# produced the anchor `Agents\_tools\axis_hist.py` -- the tail of
# `D:\zo\Zocomputer Agents\_tools\axis_hist.py`, sheared at the space in
# "Zocomputer Agents". A path regex that meets a path with a space in it yields a
# plausible-looking lie. Since every downstream use normalises to the basename
# anyway (`_norm`), matching the basename directly removes the whole class of bug
# rather than patching around the one instance of it.
ANCHOR_RE = re.compile(
    r"(?<![\w.])[\w-]+(?:\.[\w-]+)*\.(?:py|sh|sql|ps1|toml|ya?ml|json)(?![\w])"
)


def _norm(anchor: str) -> str:
    """Compare anchors on basename, case-folded. The ledger writes
    `tools/rescore/delta_report.py`, `delta_report.py` and
    `D:\\zo\\...\\delta_report.py` for the same file."""
    return os.path.basename(str(anchor).replace("\\", "/")).casefold()


def _load_json(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_index(agents_dir: Path) -> Dict[str, Any]:
    return _load_json(agents_dir / "graphify" / "_fu_index.json")


def load_drift(agents_dir: Path) -> Dict[str, Any]:
    return _load_json(agents_dir / "graphify" / "fu_anchor_drift_last.json")


def anchors_from_ledger(ledger: Path, fu_num: str) -> tuple[List[str], str, str]:
    """Fallback when the FU is absent from `_fu_index.json` -- that index only
    covers FUs the daily refresh has seen, so a same-day entry is never in it."""
    if not ledger.is_file():
        return [], "", ""
    lines = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    want = fu_num.lstrip("0") or "0"
    for fu in fu_ledger.parse(lines):
        if (fu.num.lstrip("0") or "0") == want:
            body = "".join(lines[fu.start:fu.end])
            found: List[str] = []
            seen: set[str] = set()
            for m in ANCHOR_RE.finditer(body):
                tok, k = m.group(0), _norm(m.group(0))
                if k not in seen:
                    seen.add(k)
                    found.append(tok)
            return found, fu.status_raw, fu.title
    return [], "", ""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def classify_unresolved(anchors: List[str], root: Path) -> Dict[str, List[str]]:
    """Split unresolved anchors by WHY, so the count means something.

    out_of_scope      -- no such basename anywhere in the repo: the KL was never
                         going to hold it (tower-local file). NOT breakage.
    in_repo_unindexed -- the file IS here but the graph has no node for it:
                         genuine graph staleness, worth an alarm.
    """
    present: set[str] = set()
    try:
        for p in root.rglob("*"):
            if p.is_file():
                present.add(p.name.casefold())
    except OSError:
        pass
    out: Dict[str, List[str]] = {"out_of_scope": [], "in_repo_unindexed": []}
    for a in anchors:
        key = "in_repo_unindexed" if _norm(a) in present else "out_of_scope"
        out[key].append(a)
    return out


def bus_neighbors(anchors: List[str]) -> tuple[Any | None, str]:
    """1-hop expansion over the :8772 DuckDB code_nodes/code_edges bus.

    NEVER raises: returns (None, reason) when the bus is unreachable, because an
    unreachable graph must degrade to a smaller answer rather than an error.
    """
    if not anchors:
        return None, "no anchors to expand"
    quoted = ",".join("'" + _norm(a).replace("'", "''") + "'" for a in anchors)
    sql = (
        "SELECT n.id AS node, n.source_file AS file, e.rel AS rel, "
        "m.id AS neighbour, m.source_file AS neighbour_file "
        "FROM code_nodes n "
        "JOIN code_edges e ON e.src = n.id "
        "JOIN code_nodes m ON m.id = e.dst "
        f"WHERE lower(regexp_extract(n.source_file, '[^/\\\\]+$')) IN ({quoted}) "
        "LIMIT 400"
    )
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{BUS_URL}/query",
            data=json.dumps({"sql": sql}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=BUS_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8")), "ok"
    except Exception as exc:  # noqa: BLE001 -- any failure degrades, none propagates
        return None, f"{type(exc).__name__}: {exc}"


def build_context(fu_num: str, extra_anchors: List[str], agents_dir: Path,
                  ledger: Path, use_bus: bool) -> Dict[str, Any]:
    key = f"FU-{fu_num.zfill(3)}"
    index = load_index(agents_dir)
    entry = index.get(key) or index.get(f"FU-{fu_num}") or {}

    anchors: List[str] = list(entry.get("anchors") or [])
    status = entry.get("status", "")
    title = entry.get("title", "")
    source = "_fu_index.json"

    if not anchors:
        led_anchors, led_status, led_title = anchors_from_ledger(ledger, fu_num)
        if led_anchors or led_title:
            anchors = led_anchors
            status = status or led_status
            title = title or led_title
            source = "FOLLOWUPS.md (not yet in _fu_index.json)"

    known = {_norm(x) for x in anchors}
    for a in extra_anchors:
        if _norm(a) not in known:
            known.add(_norm(a))
            anchors.append(a)

    drift_doc = load_drift(agents_dir)
    drift_entry = (drift_doc.get("drift") or {}).get(key, {})
    unresolved = list(drift_entry.get("unresolved") or [])

    ctx: Dict[str, Any] = {
        "fu": key,
        "title": title,
        "status": status,
        "anchor_source": source,
        "anchors": anchors,
        "graph_commit": drift_doc.get("graph_commit", ""),
        "drift_generated": drift_doc.get("generated", ""),
        "unresolved": classify_unresolved(unresolved, repo_root()),
    }

    if not use_bus:
        ctx["kl"] = "skipped (--dry-run)"
        ctx["subgraph"] = []
        return ctx

    rows, why = bus_neighbors(anchors)
    if rows is None:
        ctx["kl"] = f"unavailable ({why})"
        ctx["subgraph"] = []
    else:
        payload = rows.get("rows", rows) if isinstance(rows, dict) else rows
        ctx["kl"] = "ok"
        ctx["subgraph"] = payload if isinstance(payload, list) else [payload]
    return ctx


def render(ctx: Dict[str, Any]) -> str:
    out = [f"{ctx['fu']}  [{ctx['status'] or '?'}]  {ctx['title']}",
           f"  anchors ({len(ctx['anchors'])}) via {ctx['anchor_source']}"]
    for a in ctx["anchors"]:
        out.append(f"    - {a}")
    u = ctx["unresolved"]
    if u["in_repo_unindexed"]:
        out.append(f"  GRAPH STALE -- in repo but no KL node "
                   f"({len(u['in_repo_unindexed'])}):")
        for a in u["in_repo_unindexed"]:
            out.append(f"    ! {a}")
    if u["out_of_scope"]:
        out.append(f"  out of scope -- tower-local; the KL indexes this repo only "
                   f"({len(u['out_of_scope'])}):")
        for a in u["out_of_scope"]:
            out.append(f"    . {a}")
    out.append(f"  kl: {ctx['kl']}   graph_commit={ctx['graph_commit'] or '?'}")
    if ctx["subgraph"]:
        out.append(f"  1-hop subgraph ({len(ctx['subgraph'])} edges):")
        for row in ctx["subgraph"][:40]:
            if isinstance(row, dict):
                out.append(f"    {row.get('file', '?')} --{row.get('rel', '?')}--> "
                           f"{row.get('neighbour', '?')}")
            else:
                out.append(f"    {row}")
    return "\n".join(out)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--fu", required=True, help="FU number, e.g. 181 or 003")
    ap.add_argument("--anchors", default="",
                    help="extra comma-separated anchors to union in")
    ap.add_argument("--agents-dir", default=str(DEFAULT_AGENTS_DIR))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="do not touch the :8772 bus; anchors + drift only")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if not re.fullmatch(r"\d{1,4}", a.fu):
        print(f"--fu must be a number, got {a.fu!r}", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    agents_dir = Path(a.agents_dir)
    ledger = Path(a.ledger) if a.ledger else agents_dir / "FOLLOWUPS.md"

    ctx = build_context(a.fu, [x.strip() for x in a.anchors.split(",") if x.strip()],
                        agents_dir, ledger, use_bus=not a.dry_run)

    # Cannot evaluate: unknown to BOTH stores. Distinct from an FU that
    # legitimately carries no anchors yet -- that one still prints and exits 0.
    if not ctx["title"] and not ctx["anchors"]:
        print(f"CANNOT EVALUATE: {ctx['fu']} not found in _fu_index.json or "
              f"{ledger} (and no anchors supplied)", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    print(json.dumps(ctx, indent=2) if a.json else render(ctx))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
