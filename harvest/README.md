# MCP discovery harvest → Fly registry

This directory is the **only** path by which anything reaches
`mcp_server_registry` in prod. Until 2026-09-10 none of it was in version
control; it ran untracked on the tower at `D:\zo\runs\sprint200k`.

## Topology

```
tower  07:01 ET  harvest_refresh.py  ──►  D:\zo\runs\sprint200k\refresh_<date>_since<d>.jsonl
zo     18:00 ET  harvest_refresh.py  ──►  <same dir, via mirror>\refresh_<date>_zo_since<d>.jsonl
                                             │
tower  (after AM harvest)  sprint_import.py ─┘──►  Fly Postgres  mcp_server_registry
```

**Two producers, one importer.** Only the tower holds `FLY_API_TOKEN`, so the
import leg stays there; the Zo producer drops its jsonl into the mirrored
directory and the tower's next run globs it. `sprint_import.py` is idempotent
(`ON CONFLICT DO NOTHING`) plus an anti-filter against server_ids prod already
has, so re-globbing the same files is cheap and safe.

### Running it

`harvest_refresh.py` is now **one file for both hosts**. It previously carried a
hardcoded `DIR = r"D:\zo\runs\sprint200k"`, which is exactly why a second
hand-edited copy grew on Zo. Two env vars replace that fork:

| var | tower | zo |
|---|---|---|
| `HARVEST_DIR` | unset (defaults to the tower path) | `/home/workspace/zo/runs/sprint200k` |
| `HARVEST_HOST_TAG` | unset | `zo` |

`HARVEST_HOST_TAG` lands in the output filename, so the two producers write
distinct files into the shared directory and **cannot clobber each other**.

```bash
# tower (AM)
python harvest_refresh.py --since 2026-09-08
python sprint_import.py

# zo (PM)
HARVEST_DIR=/home/workspace/zo/runs/sprint200k HARVEST_HOST_TAG=zo \
  python3 harvest_refresh.py --since 2026-09-08 --queries "mcp in:name,topic:mcp,mcp-server in:name"
```

## Identity — read before changing the key

The registry keys on `server_id = md5("github|" + full_name)[:16]`. **Repo names
are mutable and re-claimable.** Measured, not assumed — see `recon/FINDINGS.md`:

| candidate key | verdict |
|---|---|
| `full_name` | unstable — 0.75% renamed, 1.25% dead (seeded n=400) |
| `html_url` | **the same key wearing a hat** — 354,306 urls for 354,306 names, 0 collisions |
| `owner_login` | not a key — 248,246 owners for 354,306 repos; 2 of 3 renames were transfers |
| **repo `id`** | stable across rename **and** transfer — **use this** |

368 names in our own corpus already carry more than one immutable `created_at`.
A seeded 40 of them resolved live: 34 changed hands, 6 dead, **0** still held by
the original. Under `ON CONFLICT DO NOTHING` the newcomer is silently dropped
and the old row keeps its `trust_score` and `verdict`.

`emit()` now captures `gh_repo_id` / `gh_node_id`. They were **always in the
search response** — we simply never copied them — so forward capture costs zero
extra API quota. Migration `0013` gives them a home as a **nullable, non-unique**
column; nothing about insert behaviour changes yet.

**Do not add a unique index without reading `0013`'s docstring.** `server_id` and
`gh_repo_id` are two conflict targets, one INSERT names one target, and a row
colliding on both raises at runtime rather than skipping — it would fail the
nightly import closed.

## Known rough edges (documented, not fixed here)

- `harvest_refresh.py` opens its output `"a"` (append). Re-running the same
  window on the same day appends the set again. `ON CONFLICT` absorbs it, but
  the file grows. Deliberately left alone — append is what makes a killed run
  resumable.
- `sprint_import.py` is Windows/flyctl-specific (PowerShell proxy reaper, the
  `DIR + r"\*.jsonl"` glob). It runs on the tower only. Vendored here verbatim
  first so its history starts from what actually ran.

## Reproducing the reconciliation

```bash
python3 recon/recon_ids.py   # phases 0,1,2 -- seeded 20260910, reproducible
python3 recon/recon_1b.py    # name-reuse + transfer screen, zero API cost
python3 recon/recon_1c.py    # live confirmation of the 368
```
