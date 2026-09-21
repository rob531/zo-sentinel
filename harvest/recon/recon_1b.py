#!/usr/bin/env python3
"""PHASE 1b -- explain the Phase 1 arithmetic, which does not close.

Phase 1 saw 354,306 distinct full_name but only 354,082 (owner,created_at)
groups, yet 565 groups held >1 name. Those cannot both be true if every name
belongs to exactly one group. So some full_name must appear under MORE THAN ONE
created_at -- and created_at is IMMUTABLE. The only way one name has two
creation dates is that the name was FREED AND RE-CLAIMED by a different repo.

That is the silent-drop / squat case, and it is measurable offline. Also
measures TRANSFERS (owner changes, created_at survives), which the
(owner, created_at) key deliberately cannot see.
"""
import json, glob, collections, itertools

SRCS = ["/home/workspace/zo/runs/sprint200k/refresh_*.jsonl",
        "/home/workspace/zo_sentinel/harvest/refresh_*.jsonl"]

name_created = collections.defaultdict(set)   # full_name -> {created_at}
created_names = collections.defaultdict(set)  # created_at -> {full_name}
owners = set()
n = 0
for pat in SRCS:
    for path in sorted(glob.glob(pat)):
        for line in open(path, encoding="utf-8"):
            try: j = json.loads(line)
            except Exception: continue
            if j.get("source") != "github": continue
            fn, ca, ol = j.get("full_name"), j.get("created_at"), j.get("owner_login")
            if not fn or not ca: continue
            n += 1
            name_created[fn].add(ca)
            created_names[ca].add(fn)
            if ol: owners.add(ol)

reused = {k: sorted(v) for k, v in name_created.items() if len(v) > 1}
print("rows=%d distinct_full_name=%d distinct_owner_login=%d" % (n, len(name_created), len(owners)))
print()
print("NAME REUSE -- one full_name carrying >1 immutable created_at: %d" % len(reused))
print("  (each is a repo name that was freed and re-claimed by a DIFFERENT repo;")
print("   under sid=md5('github|'+full_name) both collapse to ONE server_id and")
print("   the newcomer is silently discarded by ON CONFLICT DO NOTHING)")
for k, v in itertools.islice(reused.items(), 15):
    print("    %-55s %s" % (k, v))

# TRANSFERS: same created_at, different owner prefix
xfer = 0
xfer_ex = []
for ca, names in created_names.items():
    if len(names) < 2: continue
    owners_here = set(x.split("/", 1)[0] for x in names)
    if len(owners_here) > 1 and len(names) <= 4:
        xfer += 1
        if len(xfer_ex) < 12: xfer_ex.append((ca, sorted(names)))
print()
print("TRANSFER-SHAPED groups (same created_at, DIFFERENT owner, <=4 names): %d" % xfer)
print("  NOTE: created_at is second-resolution and repo creation is bursty, so this")
print("  count is an UPPER bound contaminated by coincidental same-second creations.")
print("  It is a screen, not a verdict -- Phase 2's API resolve is the verdict.")
for ca, names in xfer_ex:
    print("    %s  %s" % (ca, names))
