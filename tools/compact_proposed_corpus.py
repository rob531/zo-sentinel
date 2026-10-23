#!/usr/bin/env python3
"""
compact_proposed_corpus.py -- fold directives/proposed/ into one index.

WHY THIS EXISTS (read before deleting anything)

  The terminal markers in directives/proposed/ are LOAD-BEARING. Since
  rob531/zo-sentinel#5071, `_terminal_task_stems()` reads `<task>.json.expanded`
  markers back into the architect's dedup set. Before that fix the dedup memory
  was erased the moment a directive was handled (the promoter renames the parent
  and it stops matching the `*.json` glob), and the architect re-proposed the
  same task forever -- `build_service_risk_tier_trend` 798 times, 77.2% of a
  7,976-file corpus avoidable.

  So `rm -rf directives/proposed/*` re-opens that loop immediately. The corpus
  must be COMPACTED, not discarded: one index file carries the memory, 7,976
  inodes go away.

WHAT IT WRITES

  directives/handled_tasks.json -- {task_stem: {n, first, last, states, origins,
  address, sha256}}. `address`/`sha256` are the join to tools/arch_address.py's
  content-addressing (svc://<name> + sha256 over the spine artifact). They are
  populated when a service of that name resolves in the spine index, and null
  otherwise.

  That null is the useful signal, not a gap: a task with a high `n` and a null
  address is work the fleet keeps proposing and that never reaches a mounted
  service. Sorting the index by (address is null, n desc) is a ranked list of
  what the loop keeps failing to land.

SAFETY

  --dry-run (default) reports and writes nothing. --apply writes the index,
  tars the raw corpus to directives/archive/, verifies the tar member count
  against the scanned file count, and only then removes the loose files.
  Idempotent: re-running merges into an existing index rather than replacing it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SENTINEL = Path(os.environ.get("ZO_SENTINEL_DIR", "/home/workspace/zo_sentinel"))
DIRECTIVES = SENTINEL / "directives"
PROPOSED = DIRECTIVES / "proposed"
INDEX = DIRECTIVES / "handled_tasks.json"
ARCHIVE_DIR = DIRECTIVES / "archive"

SCHEMA = 1
TERMINAL = ("expanded", "duplicate", "rejected", "revived")
# Only settled-SUCCESS states are dedup evidence. .rejected/.revived must not
# suppress a later, better attempt -- the FU-011 boundary (see #5071).
SETTLED = ("expanded", "duplicate")

_PRODUCER_PREFIX_RE = re.compile(r"^(?:salvage_\d{14}_|gen_[0-9a-f]{8}_|svc_)")

# Candidate spine indexes written by tools/arch_address.py. First hit wins.
ADDRESS_INDEXES = (
    SENTINEL / "graphify-out" / "arch_address_index.json",
    Path("/home/workspace/zo/_c64_clone/graphify-out/arch_address_index.json"),
)


def state_of(name: str) -> str:
    m = re.search(r"\.(%s)(\.\d+)?$" % "|".join(TERMINAL), name)
    return m.group(1) if m else "other"


def origin_of(name: str) -> str:
    if name.startswith("salvage_"):
        return "salvage"
    if re.match(r"^gen_[0-9a-f]{8}_", name):
        return "gen"
    if name.startswith("svc_"):
        return "svc"
    return "other"


def task_of(name: str) -> str:
    base = re.sub(r"\.(%s)(\.\d+)?$" % "|".join(TERMINAL), "", name)
    if base.endswith(".json"):
        base = base[: -len(".json")]
    return _PRODUCER_PREFIX_RE.sub("", base)


def load_addresses() -> dict:
    """name -> {address, sha256} from the arch_address spine index, if present."""
    for p in ADDRESS_INDEXES:
        try:
            if not p.is_file():
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
            out = {}
            for svc in doc.get("services", []) or []:
                n = svc.get("name")
                if n:
                    out[n] = {"address": svc.get("address"),
                              "sha256": (svc.get("sha256") or "")[:12] or None}
            if out:
                sys.stderr.write("address index: %s (%d services, root=%s)\n"
                                 % (p, len(out), (doc.get("root_digest") or "")[:12]))
                return out
        except Exception as exc:
            sys.stderr.write("address index unreadable (%s): %s\n" % (p, exc))
    sys.stderr.write("address index: none found -- addresses will be null\n")
    return {}


def resolve(task: str, addrs: dict):
    """Best-effort service match for a task stem. Exact-ish only: a wrong match
    would assert something exists when it does not, which is the failure mode
    this whole exercise is about. Prefer a null over a guess."""
    if not addrs:
        return None, None
    cand = re.sub(r"^(build_service_|build_|scaffold_|wire_)", "", task)
    for key in (task, cand, cand + "_api", cand.removesuffix("_api")):
        if key in addrs:
            return addrs[key]["address"], addrs[key]["sha256"]
    return None, None


def scan():
    if not PROPOSED.is_dir():
        return {}, 0
    files = [p for p in PROPOSED.iterdir() if p.is_file()]
    agg = defaultdict(lambda: {"n": 0, "first": None, "last": None,
                               "states": Counter(), "origins": Counter()})
    for p in files:
        t = task_of(p.name)
        if not t:
            continue
        d = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        e = agg[t]
        e["n"] += 1
        e["states"][state_of(p.name)] += 1
        e["origins"][origin_of(p.name)] += 1
        e["first"] = d if e["first"] is None else min(e["first"], d)
        e["last"] = d if e["last"] is None else max(e["last"], d)
    return agg, len(files)


def merge_existing(tasks: dict) -> dict:
    """Idempotent re-run: union with whatever the index already holds."""
    if not INDEX.is_file():
        return tasks
    try:
        old = json.loads(INDEX.read_text(encoding="utf-8")).get("tasks", {})
    except Exception:
        return tasks
    for t, o in old.items():
        if t not in tasks:
            tasks[t] = o
            continue
        cur = tasks[t]
        cur["n"] += int(o.get("n") or 0)
        for k in ("states", "origins"):
            for kk, vv in (o.get(k) or {}).items():
                cur[k][kk] = cur[k].get(kk, 0) + vv
        if o.get("first"):
            cur["first"] = min(cur["first"] or o["first"], o["first"])
        if o.get("last"):
            cur["last"] = max(cur["last"] or o["last"], o["last"])
    return tasks



def _assert_consumer_is_index_aware() -> list:
    """Refuse to delete the markers unless the code that reads them can read
    the index instead.

    ORDERING HAZARD (hit for real on 2026-09-14): the markers were archived at
    17:55 while the running generator had been started at 17:42, before the
    index-aware patch landed. That process had neither the markers (deleted)
    nor the index (its in-memory code did not read it), so dedup collapsed to
    the pre-fix state and three repeat proposals got through in 13 minutes.

    Deleting the only copy of a memory is safe only once its reader can reach
    the new copy -- AND the reader has been restarted to pick that up. The
    first half is checkable here. The second half is not, so it is shouted.
    """
    problems = []
    try:
        src = (SENTINEL / "zo_sentinel"
               / "sentinel_directive_generator_goose.py").read_text(encoding="utf-8")
        if "handled_tasks.json" not in src:
            problems.append(
                "sentinel_directive_generator_goose.py does not read "
                "handled_tasks.json -- deleting the markers would erase the "
                "dedup memory outright. Land the index-aware change first.")
    except Exception as exc:
        problems.append("could not verify the consumer reads the index (%s)" % exc)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the index, archive the corpus, remove the files")
    ap.add_argument("--keep-files", action="store_true",
                    help="with --apply: write index and tarball but do not delete")
    args = ap.parse_args()

    agg, nfiles = scan()
    if not nfiles:
        print("proposed/ is empty -- nothing to compact.")
        return 0

    addrs = load_addresses()
    agg = merge_existing(agg)

    tasks, settled = {}, 0
    for t, e in agg.items():
        address, sha = resolve(t, addrs)
        st = dict(e["states"])
        if any(st.get(s) for s in SETTLED):
            settled += 1
        tasks[t] = {"n": e["n"], "first": e["first"], "last": e["last"],
                    "states": st, "origins": dict(e["origins"]),
                    "address": address, "sha256": sha}

    doc = {
        "schema": SCHEMA,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_corpus": {"files": nfiles, "distinct_tasks": len(tasks)},
        "note": ("Dedup memory for the architect. Read by "
                 "_terminal_task_stems(); only .expanded/.duplicate entries "
                 "suppress a re-proposal. address/sha256 join to "
                 "tools/arch_address.py."),
        "tasks": tasks,
    }

    unresolved = [(t, v["n"]) for t, v in tasks.items()
                  if v["address"] is None and v["n"] >= 10]
    unresolved.sort(key=lambda x: -x[1])

    print("corpus files          : %d" % nfiles)
    print("distinct tasks        : %d" % len(tasks))
    print("tasks with settled ev.: %d  (these suppress re-proposals)" % settled)
    print("resolved to a service : %d" % sum(1 for v in tasks.values() if v["address"]))
    print()
    print("top proposed-but-never-mounted (n>=10), the real work signal:")
    for t, n in unresolved[:10]:
        print("   %5d  %s" % (n, t))

    if not args.apply:
        print("\n--dry-run: nothing written. Re-run with --apply.")
        return 0

    problems = _assert_consumer_is_index_aware()
    if problems and not args.keep_files:
        print("\nREFUSING TO DELETE:")
        for p_ in problems:
            print("  - %s" % p_)
        print("  (re-run with --keep-files to write the index and tarball only)")
        return 6

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tarball = ARCHIVE_DIR / ("proposed_corpus_%s.tar.gz" % stamp)

    INDEX.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    print("\nwrote %s (%d tasks)" % (INDEX, len(tasks)))

    files = [p for p in PROPOSED.iterdir() if p.is_file()]
    with tarfile.open(tarball, "w:gz") as tf:
        for p in files:
            tf.add(p, arcname="proposed/" + p.name)
    with tarfile.open(tarball, "r:gz") as tf:
        members = sum(1 for m in tf.getmembers() if m.isfile())
    if members != len(files):
        print("ABORT: tar holds %d members, scanned %d files. Files NOT removed."
              % (members, len(files)))
        return 5
    print("archived %d files -> %s (%.1f MB)"
          % (members, tarball, tarball.stat().st_size / 1e6))

    if args.keep_files:
        print("--keep-files: loose files left in place.")
        return 0

    removed = 0
    for p in files:
        try:
            p.unlink()
            removed += 1
        except Exception as exc:
            print("could not remove %s: %s" % (p.name, exc))
    print("removed %d loose files; %d remain"
          % (removed, sum(1 for _ in PROPOSED.iterdir())))
    print()
    print("!" * 70)
    print("RESTART ANY RUNNING CONSUMER NOW.")
    print("A daemon started before the index-aware change has neither the")
    print("markers (just deleted) nor the index (not in its loaded code).")
    print("That window has no dedup at all -- it happened on 2026-09-14 and")
    print("let three repeat proposals through in 13 minutes.")
    print("  pkill -f sentinel_directive_generator_goose  # wrapper respawns")
    print("!" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
