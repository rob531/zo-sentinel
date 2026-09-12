#!/usr/bin/env python3
"""requeue_quarantined.py -- rate-limited re-emission of the #4070 quarantine.

WHY THIS EXISTS (GitHub issue #4079, filed 2026-08-26, untouched for 8 days).
#4070 withdrew 69 pre-2026-08-11 emissions that named phantom tables into
`quarantine/`. They were withdrawn as a class rather than deleted because the
now-grounded engine path (`_schema_ground_context`, #3235) can regenerate them.
The manifest recorded the queue as DATA and stopped there, with the note "69
live build directives would open 69 PRs at once". That note is prose: it asks
whoever reads it to pace themselves. Eight days of nobody reading it is the
measurement that prose does not pace anything. This module is the pacing, in
code -- a per-run cap and an in-flight cap that a cron, a lane, or a human
hitting up-arrow all hit identically.

WHAT IT DOES NOT DO. It does not decide that a module deserves to exist. The
issue says "not every candidate necessarily deserves re-emission. Some may be
genuinely dead". That call is made here from EVIDENCE, not taste: a candidate
is eligible only when something still tracked on main REFERENCES it by module
stem (excluding self-references, quarantine/ itself, and .patch_backups/).
Measured 2026-09-03 against 35d6434: 18 of the 69 candidates are referenced at
all, 17 after self-references are dropped. The other 51 would regenerate code
with no caller -- re-emitting those is how a quarantine becomes a landfill.
`--policy all` exists so that judgement can be overridden explicitly, on the
command line, by someone who says so.

IDEMPOTENCE IS RESOLVED AGAINST LIVE STATE, NOT A LEDGER. A state file would
be a second copy of the truth and would drift from it (the diverged-ledger
class). A candidate is considered already handled when EITHER:
  1. a file exists at its original path in the repo -- it came back; or
  2. a directive carrying its stable id exists anywhere in the directives tree
     (pending/, done/, top level) or has a `<id>.done.json` sentinel.
Both are observations of the world. Re-running this tool with the same
arguments therefore emits nothing new, which is what makes it safe to schedule.

STABLE IDS, NEVER A COUNTER. inject_directive.py names its files
`{len(glob)+1:03d}_{task}.json`. A count is a shared basename: two writers, or
one writer after a deletion, collide and one directive silently overwrites the
other. Every directive written here is named for its stable id
`reemit_<stem>`, so a collision is a REFUSAL, not an overwrite.

Usage:
  python tools/requeue_quarantined.py                      # dry run, shows the batch
  python tools/requeue_quarantined.py --emit               # writes <=3 directives
  python tools/requeue_quarantined.py --emit --limit 5     # the hard cap
  python tools/requeue_quarantined.py --status             # counts only, no selection
Exit codes: 0 ok (including "nothing to do"), 2 manifest unreadable/malformed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO / "quarantine" / "QUARANTINE_2026-08-26.json"

# The directives tree the builder polls. Overridable so this is testable off
# the tower; the default is the one inject_directive.py and goose_runner.py
# both use, because a second opinion about where directives live is how a
# re-emission lands somewhere nothing reads.
DEFAULT_DIRECTIVES_DIR = Path(
    os.environ.get("ZO_DIRECTIVES_DIR", "/home/workspace/zo_sentinel/directives"))

WRITE_SERVICE = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")

# HARD caps. --limit may lower these, never raise them.
MAX_PER_RUN = 5
MAX_IN_FLIGHT = 6

# Directories whose contents are not evidence that a module is still wanted.
_NOT_A_REFERENCE = ("quarantine/", ".patch_backups/", "__pycache__/")

ID_PREFIX = "reemit_"


# ---------------------------------------------------------------- manifest ---

def load_manifest(path: Path) -> dict:
    """Read the quarantine manifest. Raises ValueError with a readable reason."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("manifest not found: %s" % path)
    except Exception as exc:                                    # noqa: BLE001
        raise ValueError("manifest unreadable (%s): %s" % (type(exc).__name__, path))
    if not isinstance(data, dict):
        raise ValueError("manifest is not an object: %s" % path)
    re_em = data.get("re_emission")
    if not isinstance(re_em, dict) or not isinstance(re_em.get("candidates"), list):
        raise ValueError("manifest has no re_emission.candidates array: %s" % path)
    return data


def stem_of(candidate: str) -> str:
    """Module stem for a candidate path. 'a/b/logic.py' -> 'logic'."""
    name = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".py"):
        name = name[:-3]
    return name.split(".")[0]


# Names that identify nothing. A candidate whose only searchable token is one
# of these cannot have its references MEASURED by text search, and the honest
# answer is UNKNOWN -- which is not zero and is also not evidence of life.
# This is not hypothetical: the first version of this file searched for the
# bare stem, so `services/staged/axis_evidence/__init__.py` searched for
# "__init__", matched 2,800 files, scored the highest reference count in the
# manifest, and was ranked FIRST for re-emission. A ranking signal that puts
# the least-identifiable files at the top is worse than no ranking at all.
_GENERIC = frozenset((
    "__init__", "__main__", "logic", "main", "app", "api", "run", "runner",
    "utils", "util", "models", "model", "config", "settings", "core", "base",
    "handler", "handlers", "service", "services", "test", "tests", "setup",
))

# Directory names that are containers, not identities: the parent of
# `services/staged/foo/logic.py` that identifies it is `foo`, not `staged`.
_CONTAINER_DIRS = frozenset((
    "services", "staged", "active", "app", "tools", "tests", "src", "lib",
    "zo_sentinel", "quarantine",
))


def search_token(candidate: str):
    """(token, kind) -- the string whose presence in another file is evidence
    that this candidate is still wanted, and how it was derived.

    kind is one of "stem" (a top-level module names itself), "package" (a
    packaged module is identified by its own directory), or "unmeasurable"
    (every available token is generic; see _GENERIC).
    """
    parts = candidate.replace("\\", "/").split("/")
    stem = stem_of(candidate)
    if len(parts) > 1:
        parent = parts[-2]
        if parent not in _CONTAINER_DIRS and parent.lower() not in _GENERIC:
            return parent, "package"
    if stem.lower() in _GENERIC:
        return "", "unmeasurable"
    return stem, "stem"


def directive_id_for(candidate: str) -> str:
    """Stable, collision-visible id. Path-qualified so two 'logic.py' under
    different service dirs do not share one id -- a shared basename is a shared
    counter, and that class has already eaten backups on this tower."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", candidate.replace(".py", "")).strip("_")
    return ID_PREFIX + slug.lower()


# --------------------------------------------------------------- evidence ---

def _corpus(repo: Path):
    """Every tracked-looking .py outside quarantine/ and the backup dirs."""
    for p in repo.rglob("*.py"):
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".git/") or any(part in rel for part in _NOT_A_REFERENCE):
            continue
        try:
            yield rel, p.read_text(encoding="utf-8", errors="replace")
        except Exception:                                       # noqa: BLE001
            continue


def reference_counts(repo: Path, candidates) -> dict:
    """{candidate: {"kind": ..., "token": ..., "refs": [...]}}.

    A module is 'still wanted' iff some OTHER live file names its token.
    Self-references are excluded: on 35d6434
    services/staged/directive_queue_health_api/logic.py scored 1 purely from
    its own text, which would have read as live.

    An "unmeasurable" candidate gets refs=[] AND kind="unmeasurable", which
    callers must not collapse into "0 references". Absence of a measurement is
    not a measurement of absence.
    """
    toks, out = {}, {}
    for c in candidates:
        token, kind = search_token(c)
        out[c] = {"kind": kind, "token": token, "refs": []}
        if kind != "unmeasurable":
            toks[c] = re.compile(r"\b" + re.escape(token) + r"\b")
    for rel, txt in _corpus(repo):
        for c, pat in toks.items():
            cpath = c.replace("\\", "/")
            if rel == cpath or rel.startswith(cpath.rsplit("/", 1)[0] + "/") \
                    and out[c]["kind"] == "package":
                continue                    # the candidate's own package
            if pat.search(txt):
                out[c]["refs"].append(rel)
    return out


# ------------------------------------------------------------ live checks ---

def already_back(repo: Path, candidate: str) -> bool:
    """The file exists again at its original path -- re-emission landed."""
    return (repo / candidate).is_file()


def existing_directive_paths(directives_dir: Path, did: str):
    """Every path in the directives tree that already carries this id."""
    d = Path(directives_dir)
    if not d.is_dir():
        return []
    found = []
    for p in d.rglob("*.json"):
        name = p.name
        if name == "%s.json" % did or name == "%s.done.json" % did:
            found.append(p)
        elif name.endswith("_%s.json" % did):
            found.append(p)
    return found


def in_flight(directives_dir: Path):
    """Re-emission directives already queued or running (not yet done)."""
    d = Path(directives_dir)
    if not d.is_dir():
        return []
    out = []
    for p in d.rglob("*.json"):
        if not p.name.startswith(ID_PREFIX) or p.name.endswith(".done.json"):
            continue
        did = p.name[:-len(".json")]
        if (d / ("%s.done.json" % did)).exists():
            continue
        if "done" in p.relative_to(d).parts:
            continue
        out.append(p)
    return out


# ------------------------------------------------------------- selection ----

def plan(repo: Path, manifest: dict, directives_dir: Path,
         limit: int = 3, policy: str = "referenced") -> dict:
    """Decide the batch. Pure: reads the world, writes nothing."""
    cands = list(manifest["re_emission"]["candidates"])
    by_file = {f.get("from"): f for f in (manifest.get("files") or [])
               if isinstance(f, dict)}
    refs = reference_counts(repo, cands)

    rows, skipped = [], []
    for c in cands:
        meta = by_file.get(c, {})
        ev = refs[c]
        row = {
            "candidate": c,
            "directive_id": directive_id_for(c),
            "ref_kind": ev["kind"],
            "ref_token": ev["token"],
            "ref_count": len(ev["refs"]),
            "refs": ev["refs"][:4],
            "phantom_tables": meta.get("phantom_tables", []),
            "first_added_to_main": meta.get("first_added_to_main"),
        }
        if already_back(repo, c):
            row["skip"] = "already back on main"
            skipped.append(row)
            continue
        held = existing_directive_paths(directives_dir, row["directive_id"])
        if held:
            row["skip"] = "directive exists: %s" % held[0].name
            skipped.append(row)
            continue
        if policy == "referenced" and ev["kind"] == "unmeasurable":
            # UNKNOWN, not zero -- reported as its own bucket so it can never
            # be read as "we checked and nothing uses it".
            row["skip"] = "references unmeasurable (generic module name)"
            skipped.append(row)
            continue
        if policy == "referenced" and row["ref_count"] == 0:
            row["skip"] = "no live referrer (policy=referenced)"
            skipped.append(row)
            continue
        rows.append(row)

    # Most-referenced first: the modules something still calls are the ones
    # whose absence is actually costing the product something.
    rows.sort(key=lambda r: (-r["ref_count"], r["candidate"]))

    cap = max(0, min(int(limit), MAX_PER_RUN))
    flying = in_flight(directives_dir)
    headroom = max(0, MAX_IN_FLIGHT - len(flying))
    batch = rows[: min(cap, headroom)]

    return {
        "eligible": rows,
        "skipped": skipped,
        "batch": batch,
        "in_flight": [p.name for p in flying],
        "caps": {"limit": cap, "max_per_run": MAX_PER_RUN,
                 "max_in_flight": MAX_IN_FLIGHT, "headroom": headroom},
    }


# -------------------------------------------------------------- emission ----

_PROMPT = """Regenerate {candidate} for zo-sentinel.

The previous version of this file was WITHDRAWN to quarantine/{candidate} on
2026-08-26 (issue #4070) because it referenced database tables that do not
exist: {phantom}. It was first added to main on {added}, before the schema
grounding fix (#3235, 2026-08-11), so it is sediment from an ungrounded
emitter rather than a deliberate design.

Write the complete replacement file. Keep the module's purpose and public
surface; the quarantined copy is attached for reference and is the source of
truth for WHAT this module is for -- and is NOT a source of truth for any
table or column name it mentions.

Hard constraint: {phantom_clause} If this module posts SQL to the write
service at 127.0.0.1:8772, every table it names must appear in the REAL BUS
TABLES list attached to this prompt. A module name is not a table name.
"""


def build_directive(row: dict) -> dict:
    phantom = ", ".join(row.get("phantom_tables") or []) or "(none recorded)"
    tables = row.get("phantom_tables") or []
    if tables:
        clause = ("do NOT reintroduce %s -- %s not exist." %
                  (", ".join(tables), "they do" if len(tables) > 1 else "it does"))
    else:
        clause = "do not invent a table name."
    desc = _PROMPT.format(
        candidate=row["candidate"], phantom=phantom, phantom_clause=clause,
        added=row.get("first_added_to_main") or "an unrecorded date")
    return {
        "task": row["directive_id"],
        "directive_id": row["directive_id"],
        "handler": "generate_file",
        "description": desc,
        "output_file": row["candidate"],
        "complexity": "medium",
        "priority": 0.7,
        "context": "re-emission of quarantined module (#4079, from #4070)",
        "reads": ["quarantine/%s" % row["candidate"], "app/models.py"],
        "from": "requeue_quarantined",
        "reemission": {
            "issue": 4079,
            "quarantined_by": 4070,
            "phantom_tables": tables,
            "first_added_to_main": row.get("first_added_to_main"),
            "ref_count": row["ref_count"],
        },
    }


def emit(directives_dir: Path, directive: dict, post_to_bus: bool = True) -> Path:
    """Write ONE directive. Refuses to overwrite; the caller sees the exception."""
    d = Path(directives_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("%s.json" % directive["directive_id"])
    if path.exists():
        raise FileExistsError("directive already exists, refusing to overwrite: %s"
                              % path)
    directive = dict(directive)
    directive["injected_at"] = datetime.now(timezone.utc).isoformat()
    # Exclusive create: two runners racing must not both think they wrote it.
    with open(path, "x", encoding="utf-8", newline="\n") as fh:
        json.dump(directive, fh, indent=2)
    if post_to_bus:
        _post(directive)
    return path


def _post(directive: dict) -> bool:
    """Best effort mesh_memory row, exactly the shape inject_directive uses.
    The FILE is the durable half; the bus row is the fast path. A bus that is
    down must not stop a re-emission, and must not be reported as if it were up.
    """
    try:
        import requests                                        # noqa: PLC0415
        r = requests.post(
            "%s/write" % WRITE_SERVICE,
            json={"table": "mesh_memory", "wait": True, "rows": {
                "agent_id": "zo_sentinel.directive",
                "memory_type": "build_directive",
                "content": json.dumps(directive),
                "importance": directive.get("priority", 0.7),
                "created_at": directive["injected_at"],
            }},
            timeout=8)
        return r.status_code == 200
    except Exception:                                          # noqa: BLE001
        return False


# ------------------------------------------------------------------- CLI ----

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--directives-dir", default=str(DEFAULT_DIRECTIVES_DIR))
    ap.add_argument("--limit", type=int, default=3,
                    help="directives this run (hard cap %d)" % MAX_PER_RUN)
    ap.add_argument("--policy", choices=["referenced", "all"], default="referenced")
    ap.add_argument("--emit", action="store_true",
                    help="actually write directives (default is a dry run)")
    ap.add_argument("--no-bus", action="store_true",
                    help="write the directive file only, skip the mesh_memory row")
    ap.add_argument("--status", action="store_true",
                    help="counts only; never selects or emits")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.manifest))
    except ValueError as exc:
        print("MANIFEST ERROR: %s" % exc, file=sys.stderr)
        return 2

    repo = Path(args.repo)
    ddir = Path(args.directives_dir)
    p = plan(repo, manifest, ddir, limit=args.limit, policy=args.policy)

    total = len(manifest["re_emission"]["candidates"])
    done = [r for r in p["skipped"] if r["skip"] == "already back on main"]
    held = [r for r in p["skipped"] if r["skip"].startswith("directive exists")]
    dead = [r for r in p["skipped"] if r["skip"].startswith("no live referrer")]
    unk = [r for r in p["skipped"] if r["skip"].startswith("references unmeasurable")]

    summary = {
        "candidates": total,
        "back_on_main": len(done),
        "directive_already_queued": len(held),
        "no_live_referrer": len(dead),
        "references_unmeasurable": len(unk),
        "eligible_now": len(p["eligible"]),
        "in_flight": len(p["in_flight"]),
        "batch": [r["candidate"] for r in p["batch"]],
        "caps": p["caps"],
        "emitted": [],
        "dry_run": not args.emit,
    }

    if args.emit and not args.status:
        for row in p["batch"]:
            try:
                path = emit(ddir, build_directive(row), post_to_bus=not args.no_bus)
                summary["emitted"].append(str(path))
            except FileExistsError as exc:
                print("SKIP (raced): %s" % exc, file=sys.stderr)
        summary["dry_run"] = False

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("quarantine re-emission (#4079) -- manifest %s" % args.manifest)
    print("  candidates                %d" % total)
    print("  already back on main      %d" % len(done))
    print("  directive already queued  %d" % len(held))
    print("  no live referrer (skipped)%d   [policy=%s]" % (len(dead), args.policy))
    print("  refs UNMEASURABLE         %d   (generic module name -- UNKNOWN, not zero)"
          % len(unk))
    print("  eligible now              %d" % len(p["eligible"]))
    print("  in flight                 %d / %d" % (len(p["in_flight"]), MAX_IN_FLIGHT))
    print("  this run                  %d (limit %d, headroom %d)"
          % (len(p["batch"]), p["caps"]["limit"], p["caps"]["headroom"]))
    if args.status:
        return 0
    for r in p["batch"]:
        print("    %-8s %-52s refs=%d %s"
              % (("EMIT" if args.emit else "would"), r["candidate"],
                 r["ref_count"], r["refs"][:2]))
    for path in summary["emitted"]:
        print("  wrote %s" % path)
    if not args.emit:
        print("  (dry run -- pass --emit to write these)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
