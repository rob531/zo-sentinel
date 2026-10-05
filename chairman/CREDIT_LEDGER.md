# Credit ledger: what the fleet can still spend

Started 2026-10-03 (finding 2026-10-02 item 7, GR-21). Read by
`creds_runway.py` (zo-fleet-tools), which exits 1 when a provider is SHORT of
its reserved work or EXPIRING, and 2 when this file is missing or unparseable.

Rules: `?` = unknown (a value, not zero), `-` = not applicable. Re-measure a
balance before quoting it; a number in this file only says which console to
check. Every cloud session appends one row to Sessions before it ends, with
`?` if it cannot see its own spend.

## Balances (one row per provider; edit in place when re-measured)
| provider | balance_usd | measured_at (UTC) | expires (UTC) | source |
|---|---|---|---|---|
| claude-cloud-credit | 66.00 | 2026-10-04T23:00Z | 2026-10-08 | Robin in-session 2026-10-04 ~23:00Z (grant $250) |
| anthropic-api | 1.00 | 2026-09-29 | - | console |
| mistral | 0.00 | 2026-09-29 | - | out |
| vast | 5.50 | 2026-09-29 | - | invoices API |

## Reserved (work that must stay fundable)
| chain | provider | ceiling_usd | note |
|---|---|---|---|
| auth_evidence E14b | anthropic-api | 6.00 | |
| E16 | vast | 1.50 | |
| moat weekly wave | vast | 3.00 | per week |

## Sessions (append one row per cloud session)
| date (UTC) | provider | spend_usd | session | what |
|---|---|---|---|---|
| 2026-10-03 | claude-cloud-credit | ? | session_01To4EdKyYczpEpTZv3Wa465 | store roles (GR-19..22), dead alert checks, credit ledger; three sub-agents. List price 43.08 by 2026-10-05T00:30Z, nearly all before the 10-04 23:00Z balance read; its 10-05 spend after the read (one-pager line, this row) is small and not separated |
| 2026-10-04T22:17Z | claude-cloud-credit | 40.79 | session_01EV4jnhYUdG3pyWDEjRgPAN | staging_drain chain S1-S7 (GR-23); no vast, no paid API calls; no sub-agents. 40.79 = list price from the session's final usage report (costBasis list; billed amount unverified). It ended 22:17Z, before the 23:00Z balance read, so the 66.00 already nets it |
| 2026-10-04 | claude-cloud-credit | ? | session_01TwJ15ma5Nf91RxPTJP85nt | events store rebuilt from the 2026-10-03 packet (GR-24), zo-fleet-tools#50; Robin: >$40 spent on recovery + rebuild; two recovery sub-agents stopped early |
| 2026-10-05 | - | 0 | tower-local (GR-24 arming) | armed the events store on the tower (GR-24 C1->C3): self-test PASS + tests 6/6, baseline tick (bus 3646 == registry N, 0 events) + idempotent re-tick + `check` rc 0 + TLS baseline, Parquet backup; no vast/anthropic/cloud-credit spend. Ran from a clean zo-fleet-tools checkout; `_tools` left dirty. ZoEventsTick (C4) deferred -- chairman option C (`_tools` ff when the owning lane commits) |
| 2026-10-05 | - | 0 | tower-local (B1'' zo_call tag fuse) | fixed B1'' in zo-fleet-tools zo_call.py: a COMPLETED auto-tag no longer refuses (c170 `_rot > 9` exited rc=2, blocking a legit re-run) -- rotates past DONE until a tag runs; both guarantees preserved (live child ADOPTS; DONE never handed back as a fresh rc). Hermetic two-pole test test_b1dp_tag_fuse.py RED on origin/main (pole-a call#11 rc=2) / GREEN on fix; py_compile green both trees. zo-fleet-tools#58 merged (squash b06159e). Max sub -- no provider spend. Clean checkout; `_tools` untouched |
