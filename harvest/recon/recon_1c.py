#!/usr/bin/env python3
"""PHASE 1c -- CONFIRM the 368 offline name-reuse findings against live GitHub.

For each candidate the corpus holds >=2 immutable created_at under ONE name.
Resolve the name now. The live repo's created_at tells us WHICH of them owns
the name today. If it matches the LATER date, the earlier repo is gone and the
name changed hands -- while prod still holds a row keyed on that name.

Control: 'other' / error outcomes are reported, not silently folded into a
verdict. A candidate we cannot resolve is UNKNOWN, not clean.
"""
import json, glob, collections, subprocess, time, random
import requests

TOK = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
H = {"Authorization": "Bearer %s" % TOK, "Accept": "application/vnd.github+json"}
SRCS = ["/home/workspace/zo/runs/sprint200k/refresh_*.jsonl",
        "/home/workspace/zo_sentinel/harvest/refresh_*.jsonl"]

nc = collections.defaultdict(set)
owner_of = collections.defaultdict(set)
for pat in SRCS:
    for path in sorted(glob.glob(pat)):
        for line in open(path, encoding="utf-8"):
            try: j = json.loads(line)
            except Exception: continue
            if j.get("source") != "github": continue
            fn, ca = j.get("full_name"), j.get("created_at")
            if fn and ca:
                nc[fn].add(ca)
                owner_of[fn].add(j.get("owner_login"))

cands = sorted(k for k, v in nc.items() if len(v) > 1)
print("offline name-reuse candidates: %d" % len(cands))
random.seed(20260910)
pick = random.sample(cands, 40)

verdict = collections.Counter()
rows = []
for fn in pick:
    dates = sorted(nc[fn])
    try:
        r = requests.get("https://api.github.com/repos/%s" % fn, headers=H, timeout=25)
    except Exception as e:
        verdict["error"] += 1; rows.append((fn, dates, "ERROR", repr(e))); continue
    if r.status_code == 404:
        verdict["name_now_dead"] += 1
        rows.append((fn, dates, "404", "name free -- prod still holds a row for it"))
    elif r.status_code == 200:
        j = r.json()
        live = j.get("created_at"); rid = j.get("id")
        if live == dates[-1] and len(dates) > 1:
            verdict["LATEST_OWNS_NAME"] += 1
            rows.append((fn, dates, "live=%s id=%s" % (live, rid), "NAME CHANGED HANDS -- earlier repo(s) evicted"))
        elif live == dates[0]:
            verdict["original_still_owns"] += 1
            rows.append((fn, dates, "live=%s id=%s" % (live, rid), "original holds it; later row(s) were DROPPED on insert"))
        else:
            verdict["third_created_at"] += 1
            rows.append((fn, dates, "live=%s id=%s" % (live, rid), "live date matches NEITHER harvested date"))
    else:
        verdict["other_%d" % r.status_code] += 1
        rows.append((fn, dates, str(r.status_code), "UNKNOWN -- not counted as clean"))
    time.sleep(0.1)

print("\nVERDICT over %d resolved candidates:" % len(pick))
for k, v in verdict.most_common(): print("   %-22s %d" % (k, v))
print("\nDETAIL:")
for fn, dates, st, note in rows:
    print("  %-50s %s\n      -> %s | %s" % (fn, dates, st, note))
