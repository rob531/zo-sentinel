#!/usr/bin/env python3
"""harvest_refresh.py -- FU-054 discovery restart: widened + refreshable GitHub
search harvest, an evolution of gh_harvest.py.

WHY: the 2026-07-16 one-off harvest used only 5 narrow *topic* queries and got
78,269 repos, while the same-day universe probe measured `mcp in:name` at ~278K
(~157K confirmed MCP). It also never ran `topic:mcp` (502'd) or any name/desc
query. And nothing regenerates the inputs, so intake fell to ~2/day. This script:
  * widens the query set to the under-harvested strata (name / name+desc / topic:mcp
    + the original topics for continuity), and
  * supports --since DATE so a scheduled run harvests ONLY repos created in the
    refresh window (bounded, fast), appending net-new to a dated jsonl that
    sprint_import.py then loads idempotently (ON CONFLICT DO NOTHING).

USAGE
  python harvest_refresh.py --since 2026-07-16                 # refresh slice
  python harvest_refresh.py --full                             # full widened sweep (hours)
  python harvest_refresh.py --since 2026-07-16 --queries "mcp in:name,topic:mcp"
Then:  python sprint_import.py         # loads the new dated jsonl into prod
"""
import argparse, json, subprocess, time, datetime, os
import requests

# One file, two hosts. This was a hardcoded Windows path, which is why a
# second hand-edited copy grew on Zo. HARVEST_DIR lets both hosts run THIS
# file. Set HARVEST_HOST_TAG on the non-tower host so the two producers
# write distinct filenames into a shared drop dir and never clobber.
DIR = os.environ.get("HARVEST_DIR") or r"D:\zo\runs\sprint200k"
HOST_TAG = os.environ.get("HARVEST_HOST_TAG", "").strip()
TOK = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
H = {"Authorization": "Bearer %s" % TOK, "Accept": "application/vnd.github+json"}

# widened set: the two big under-harvested name strata + topic:mcp (never ran)
# + the original topics/name for continuity. dedup across queries via `seen`.
QUERIES_WIDE = [
    "mcp in:name", "mcp in:name,description",
    "topic:mcp", "topic:mcp-server", "topic:model-context-protocol",
    "topic:modelcontextprotocol", "mcp-server in:name",
]

def log(logpath, m):
    line = "[%s] %s" % (datetime.datetime.utcnow().isoformat(), m)
    print(line, flush=True)
    with open(logpath, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def gh_search(q, page=1, per_page=100, logpath=None):
    while True:
        r = requests.get("https://api.github.com/search/repositories",
                         params={"q": q, "per_page": per_page, "page": page,
                                 "sort": "created", "order": "asc"},
                         headers=H, timeout=30)
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time() + 5, 10)
            if logpath: log(logpath, "rate limited, sleep %.0fs" % wait)
            time.sleep(wait); continue
        if r.status_code == 422:
            return None
        if r.status_code >= 500:
            if logpath: log(logpath, "%d for q=%s p%d, retry in 15s" % (r.status_code, q, page))
            time.sleep(15); continue
        r.raise_for_status()
        time.sleep(2.2)   # ~27 search/min, under the 30/min authed budget
        return r.json()

def emit(f, item, q, seen):
    fn = item.get("full_name")
    if not fn or fn in seen:
        return 0
    seen.add(fn)
    lic = item.get("license") or {}
    owner = item.get("owner") or {}
    # STABLE IDENTITY (2026-09-10). full_name is mutable and re-claimable: a
    # measured 368 names in our own corpus already carry >1 immutable
    # created_at, 40/40 confirmed live as the name having changed hands. The
    # repo id survives BOTH rename and owner transfer. It was in this same
    # response all along -- we simply never copied it -- so capturing it costs
    # ZERO additional API quota. See harvest/recon/FINDINGS.md.
    f.write(json.dumps({"source": "github", "query": q,
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "gh_repo_id": item.get("id"), "gh_node_id": item.get("node_id"),
        "full_name": fn, "html_url": item.get("html_url"),
        "description": (item.get("description") or "")[:500],
        "stargazers_count": item.get("stargazers_count"), "forks_count": item.get("forks_count"),
        "open_issues_count": item.get("open_issues_count"), "pushed_at": item.get("pushed_at"),
        "created_at": item.get("created_at"), "archived": item.get("archived"),
        "disabled": item.get("disabled"), "fork": item.get("fork"),
        "license_spdx": lic.get("spdx_id"), "owner_login": owner.get("login"),
        "owner_type": owner.get("type")}) + "\n")
    return 1

def harvest_query(q, start, end, f, seen, logpath):
    total_new = 0
    stack = [(start, end)]
    while stack:
        a, b = stack.pop()
        shard = "%s created:%s..%s" % (q, a.isoformat(), b.isoformat())
        probe = gh_search(shard, per_page=1, logpath=logpath)
        if probe is None:
            continue
        tc = probe.get("total_count", 0)
        if tc == 0:
            continue
        if tc > 1000 and (b - a).days > 0:
            mid = a + (b - a) // 2
            stack.append((a, mid)); stack.append((mid + datetime.timedelta(days=1), b))
            continue
        pages = min((tc + 99) // 100, 10)
        for p in range(1, pages + 1):
            data = gh_search(shard, page=p, logpath=logpath)
            if data is None:
                break
            for it in data.get("items", []):
                total_new += emit(f, it, q, seen)
    log(logpath, "query done: %s net_new=%d seen_total=%d" % (q, total_new, len(seen)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD; harvest only repos created on/after this date")
    ap.add_argument("--full", action="store_true", help="full sweep from 2008 (hours)")
    ap.add_argument("--queries", help="comma-separated query override")
    ap.add_argument("--out", help="output jsonl path")
    args = ap.parse_args()

    if args.since:
        start = datetime.date.fromisoformat(args.since)
    elif args.full:
        start = datetime.date(2008, 1, 1)
    else:
        raise SystemExit("specify --since YYYY-MM-DD or --full")
    end = datetime.date.today() + datetime.timedelta(days=1)

    queries = [s.strip() for s in args.queries.split(",")] if args.queries else QUERIES_WIDE
    tag = ("full" if args.full else "since%s" % args.since.replace("-", ""))
    host = ("_" + HOST_TAG) if HOST_TAG else ""
    out = args.out or os.path.join(DIR, "refresh_%s%s_%s.jsonl" % (
        datetime.date.today().isoformat().replace("-", ""), host, tag))
    logpath = os.path.join(DIR, "harvest_refresh.log")

    log(logpath, "START refresh out=%s start=%s end=%s queries=%d" % (out, start, end, len(queries)))
    seen = set()
    with open(out, "a", encoding="utf-8") as f:
        for q in queries:
            try:
                harvest_query(q, start, end, f, seen, logpath)
            except Exception as e:
                log(logpath, "ERROR q=%s: %r" % (q, e))
    log(logpath, "DONE unique_repos=%d out=%s" % (len(seen), out))
    print("UNIQUE=%d OUT=%s" % (len(seen), out))

if __name__ == "__main__":
    main()
