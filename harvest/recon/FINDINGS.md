# Mini reconciliation — is there a stable dedup key for the MCP registry?
2026-09-10 · run from Zo · scripts in `/home/workspace/zo_sentinel/harvest/recon/`
Inputs: 38 harvested `refresh_*.jsonl` (408,410 github rows, 354,306 distinct `full_name`)

## The key in use today
```python
def sid_github(full_name): return hashlib.md5(("github|%s" % full_name).encode()).hexdigest()[:16]
INSERT INTO mcp_server_registry (...) ... ON CONFLICT (server_id) DO NOTHING
```
`server_id` is derived from the repo NAME. Names are mutable and re-claimable.

## Candidate keys, measured

| candidate | verdict | evidence |
|---|---|---|
| `full_name` | **unstable** | 0.75% renamed, 1.25% dead in a seeded n=400 sample |
| `html_url` | **identical to full_name** | 354,306 distinct urls vs 354,306 distinct names; **0** urls map to >1 name. It is `https://github.com/` + full_name wearing a hat. |
| `owner_login` (publisher) | **not a key** | 248,246 owners for 354,306 repos — not unique by construction |
| **repo `id`** (bigint) | **STABLE — use this** | immutable across rename AND transfer |
| `node_id` (string) | stable, equivalent | same identity; `id` indexes better |

## PHASE 0 — the id was always there and we threw it away
The GitHub **search** response already carries `id` and `node_id`
(`id=952238700`, `node_id=R_kgDOOMICbA`). `harvest_refresh.py:emit()` builds its
dict field by field and simply never copies them.
**Capturing the id going forward costs ZERO additional API quota — one line.**

## PHASE 1 — offline, zero API cost
`created_at` is immutable, so a name carrying two creation dates means the name
changed hands.

- **368 distinct `full_name` values carry >1 immutable `created_at`.**
- 565 `(owner_login, created_at)` groups hold >1 name — rename candidates.
- Transfer-shaped groups: 1,755 — **UPPER BOUND, contaminated** by coincidental
  same-second creations. A screen, not a verdict.

## PHASE 1c — confirming the 368 against live GitHub (n=40, seeded)
| outcome | n |
|---|---|
| name now held by the LATER repo (earlier evicted) | **34** |
| name now dead / free to claim | **6** |
| original still owns the name | **0** |
| unresolved / unknown | **0** |

**40 of 40 confirmed. Zero false positives.** In every resolvable case the name
belongs to a repo that is *not* the one prod holds a row for.

## PHASE 2 — base rates, seeded random n=400
| outcome | n | rate |
|---|---|---|
| alive, same name | 391 | 97.75% |
| **renamed** | 3 | **0.75%** |
| **gone (404)** | 5 | **1.25%** |
| other | 1 | 0.25% |

**NEGATIVE CONTROL: 20/20 mangled names returned 404 — PASS.** The probe can
detect death; a zero here would have been a false zero.

Observed renames — note 2 of 3 are OWNER TRANSFERS, which no owner-based key survives:
- `SavageCore/autobrr-mcp` → `arr-mcps/autobrr-mcp` (id 1332615861)
- `modu-ai/cowork-plugins` → `modu-ai/moai-cowork` (id 1158424301)
- `rocky3419/mcp-dev-utils` → `sivasankarsaiirlapati/mcp-dev-utils` (id 1115847234)

## What this costs the product
Extrapolated over 354,306 github rows:
- **~2,650 renamed** → re-import under the new name as a NET-NEW server, inflating
  the count the 200k tracker reports each morning.
- **~4,400 dead names** → each one free for anyone to claim.
- **≥368 measured name-takeovers already in the corpus.** Under
  `ON CONFLICT (server_id) DO NOTHING` the newcomer is **silently discarded** and
  prod's row — with its `trust_score`, `verdict` and `risk_tier` — keeps describing
  the repo that is gone. **An impostor inherits the original's assessment.**

That last line is the finding: this is an impersonation vector inside the
impersonation product, and it is *measured*, not theorised.

## Reproduce
```
python3 /home/workspace/zo_sentinel/harvest/recon/recon_ids.py   # phases 0,1,2 (seed 20260910)
python3 /home/workspace/zo_sentinel/harvest/recon/recon_1b.py   # name-reuse + transfer screen
python3 /home/workspace/zo_sentinel/harvest/recon/recon_1c.py   # live confirmation of the 368
```
Artifacts: `recon_report.json`, `recon.out`, `recon_1c.out`.
