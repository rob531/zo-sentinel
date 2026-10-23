#!/usr/bin/env python3
"""MINI RECONCILIATION -- can we key the registry on something stable?

Three arms, each with a control:

  PHASE 0  Is the GitHub repo id ALREADY in the search response we throw away?
           (1 API call. If yes, capturing it is free -- no new quota.)
  PHASE 1  OFFLINE. Scan every harvested refresh_*.jsonl. A repo's created_at is
           IMMUTABLE, so (owner_login, created_at) is a near-key that survives a
           rename. Groups holding >1 distinct full_name are rename evidence
           already sitting in our corpus. Zero API cost.
           Also tests whether html_url is any better a key than full_name.
  PHASE 2  API. Random sample of distinct full_names, GET /repos/{full_name}.
           GitHub 301-redirects a renamed repo, so a returned full_name that
           differs from the one we asked for IS a rename. Records the real id.
           NEGATIVE CONTROL: mangled names must all 404, or the probe is blind.

Writes recon_report.json + prints a summary. Read-only on all inputs.
"""
import json, glob, os, random, sys, time, collections, subprocess, datetime
import requests

OUT   = "/home/workspace/zo_sentinel/harvest/recon"
SRCS  = ["/home/workspace/zo/runs/sprint200k/refresh_*.jsonl",
         "/home/workspace/zo_sentinel/harvest/refresh_*.jsonl"]
N_SAMPLE   = 400
N_NEGCTRL  = 20
SEED       = 20260910

TOK = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
H = {"Authorization": "Bearer %s" % TOK, "Accept": "application/vnd.github+json"}

rep = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z", "seed": SEED}
def say(m):
    print(m, flush=True)

# ---------------------------------------------------------------- PHASE 0
say("=== PHASE 0: does the SEARCH response already carry a stable id? ===")
r = requests.get("https://api.github.com/search/repositories",
                 params={"q": "mcp in:name", "per_page": 1}, headers=H, timeout=30)
r.raise_for_status()
item = r.json()["items"][0]
p0 = {"has_id": "id" in item, "has_node_id": "node_id" in item,
      "sample_id": item.get("id"), "sample_node_id": item.get("node_id"),
      "sample_full_name": item.get("full_name"),
      "all_keys": sorted(item.keys())}
rep["phase0_search_response"] = p0
say("  id present in search item : %s  (value=%s)" % (p0["has_id"], p0["sample_id"]))
say("  node_id present           : %s  (value=%s)" % (p0["has_node_id"], p0["sample_node_id"]))
say("  -> capturing it costs ZERO extra API calls" if p0["has_id"] else "  -> NOT present, would need per-repo calls")

# ---------------------------------------------------------------- PHASE 1
say("\n=== PHASE 1: OFFLINE rename evidence via immutable created_at ===")
files = []
for pat in SRCS:
    files.extend(sorted(glob.glob(pat)))
say("  jsonl files scanned: %d" % len(files))

by_key   = collections.defaultdict(set)   # (owner_login, created_at) -> {full_name}
url_of   = {}                             # full_name -> html_url
name_of_url = collections.defaultdict(set)
rows = 0
names = set()
no_created = 0
for path in files:
    try:
        fh = open(path, encoding="utf-8")
    except Exception as e:
        say("  SKIP %s (%r)" % (path, e)); continue
    with fh:
        for line in fh:
            try: j = json.loads(line)
            except Exception: continue
            if j.get("source") != "github": continue
            fn = j.get("full_name")
            if not fn: continue
            rows += 1
            names.add(fn)
            u = j.get("html_url")
            if u:
                url_of[fn] = u
                name_of_url[u].add(fn)
            ca, ol = j.get("created_at"), j.get("owner_login")
            if not ca or not ol:
                no_created += 1; continue
            by_key[(ol, ca)].add(fn)

multi = {k: sorted(v) for k, v in by_key.items() if len(v) > 1}
say("  github rows=%d  distinct full_name=%d  missing created_at/owner=%d"
    % (rows, len(names), no_created))
say("  (owner_login, created_at) groups=%d" % len(by_key))
say("  groups with >1 DISTINCT full_name (rename candidates)=%d" % len(multi))

# is html_url any better a key than full_name?
url_collisions = {u: sorted(v) for u, v in name_of_url.items() if len(v) > 1}
say("  distinct html_url=%d vs distinct full_name=%d" % (len(name_of_url), len(names)))
say("  html_url values mapping to >1 full_name=%d" % len(url_collisions))

rep["phase1_offline"] = {
    "files_scanned": len(files), "github_rows": rows,
    "distinct_full_name": len(names), "distinct_html_url": len(name_of_url),
    "missing_created_at_or_owner": no_created,
    "owner_created_groups": len(by_key),
    "rename_candidate_groups": len(multi),
    "rename_candidate_examples": [{"owner": k[0], "created_at": k[1], "names": v}
                                  for k, v in list(multi.items())[:25]],
    "html_url_collisions": len(url_collisions),
    "html_url_collision_examples": list(url_collisions.items())[:10],
}
for k, v in list(multi.items())[:10]:
    say("    RENAME? owner=%s created=%s -> %s" % (k[0], k[1], v))

# ---------------------------------------------------------------- PHASE 2
say("\n=== PHASE 2: API resolve a random sample (measures rename + death rate) ===")
random.seed(SEED)
pool = sorted(names)
sample = random.sample(pool, min(N_SAMPLE, len(pool)))
say("  sample n=%d from pool=%d (seed=%d, reproducible)" % (len(sample), len(pool), SEED))

def resolve(fn):
    url = "https://api.github.com/repos/%s" % fn
    for _ in range(4):
        try:
            r = requests.get(url, headers=H, timeout=25, allow_redirects=True)
        except Exception as e:
            time.sleep(3); continue
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            time.sleep(max(reset - time.time() + 5, 10)); continue
        return r
    return None

res = {"alive_same_name": 0, "renamed": 0, "gone_404": 0, "other": 0, "error": 0}
renames, gone = [], []
t0 = time.time()
for i, fn in enumerate(sample, 1):
    r = resolve(fn)
    if r is None:
        res["error"] += 1; continue
    if r.status_code == 404:
        res["gone_404"] += 1; gone.append(fn)
    elif r.status_code == 200:
        j = r.json()
        cur = j.get("full_name")
        if cur and cur.lower() != fn.lower():
            res["renamed"] += 1
            renames.append({"harvested_as": fn, "now": cur, "id": j.get("id"),
                            "node_id": j.get("node_id")})
        else:
            res["alive_same_name"] += 1
    else:
        res["other"] += 1
    if i % 50 == 0:
        say("    ... %d/%d  %.0fs  %s" % (i, len(sample), time.time() - t0, res))
    time.sleep(0.08)
say("  RESULT %s  (%.0fs)" % (res, time.time() - t0))

# NEGATIVE CONTROL -- if these do not all 404 the probe cannot detect death
say("\n  negative control: %d mangled names must ALL 404" % N_NEGCTRL)
neg = {"404": 0, "not404": 0}
neg_bad = []
for fn in random.sample(sample, min(N_NEGCTRL, len(sample))):
    mangled = fn + "-zzz-does-not-exist-20260910"
    r = resolve(mangled)
    if r is not None and r.status_code == 404:
        neg["404"] += 1
    else:
        neg["not404"] += 1
        neg_bad.append((mangled, None if r is None else r.status_code))
    time.sleep(0.08)
say("  negative control: %s  %s" % (neg, "PASS" if neg["not404"] == 0 else "FAIL " + repr(neg_bad)))

rep["phase2_api"] = {"n": len(sample), "counts": res,
                     "rename_rate": round(res["renamed"] / max(len(sample), 1), 4),
                     "gone_rate": round(res["gone_404"] / max(len(sample), 1), 4),
                     "renames": renames[:60], "gone_examples": gone[:30],
                     "negative_control": neg, "negative_control_pass": neg["not404"] == 0}

with open(os.path.join(OUT, "recon_report.json"), "w", encoding="utf-8") as f:
    json.dump(rep, f, indent=2)
say("\nWROTE %s/recon_report.json" % OUT)
say("DONE")
