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
| claude-cloud-credit | 50.00 | 2026-10-06 | 2026-11-05 | Robin in-session 2026-10-06: expiry is 2026-11-05 (was wrongly 10-08); ~$50-60 est unspent, none logged since the 10-04 read |
| anthropic-api | 20.00 | 2026-10-06 | - | Robin in-session 2026-10-06 (+$20 topup; unblocks auth_evidence E14b) |
| mistral | 0.00 | 2026-09-29 | - | out |
| vast | 10.00 | 2026-10-06 | - | Robin in-session 2026-10-06 (refill; was 5.50) |
| glide | 5.00 | 2026-10-06 | - | Robin in-session 2026-10-06 (new LLM-gateway provider; proposer/tie-breaker only, GLIDE_DECISION_SURROGATE_POLICY.md) |

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
| 2026-10-06 | - | 0 | tower-local (glide proposer/tie-breaker + chain legibility) | wired Glide as PROPOSER + TIE-BREAKER (zo-fleet-tools#71, glide_propose.py): tier computed from authority.json not from Glide; Tier0 FORBIDDEN=still_escalate clauses+patterns, Tier1 unknown-action->peer_review, Tier2 non-HELD input->reversible decided_by:glide receipt. Two-pole: self-test 19/19 GREEN, RED 6-fail on gate-removed copy incl. malicious-Glide invariant; verified on origin/main. Also wrote GLIDE_DECISION_SURROGATE_POLICY.md, added auth_evidence E14b-FUND (attested on the +$20 anthropic topup -> E14b unblocked) + grant-chain R07 in-flight cost-guard; verified budget_guard_sentinel #60 14/14. Balances updated this session. Max sub -- no provider spend |
| 2026-10-07 | claude-cloud-credit | ? | session_01FsrXqMwHF2Jy7GtYavdXik | histo-schema + self-modification judge contract (GR-26, GR-27), [zo-fleet-tools#86](https://github.com/rob531/zo-fleet-tools/pull/86); two read-only research sub-agents. No vast, no paid API, no GLiDE call (the seam is unwired by design). This session cannot see its own spend |
| 2026-10-08 | - | 0 | tower-local (FU-620 drifted-constant false-STALE) | friction_family_census.py INVARIANT 1 was exact-equality ps-command-dollar==18 (a filing-time snapshot); the family grew to 25, so it reported a false REGRESSED and that false rc-flip made falsification_staleness falsely bill scratchpad-silent-nothing-family STALE (a re-file not owed). Changed to a baseline FLOOR (>=18) -- the over-merge guard the docstring always described; added FRICTION_LEDGER env override so the negative pole is testable. Two-pole: RED origin/main census rc=1 / _pr_probe rc=1 / falsification --id STALE; GREEN census rc=0 / _pr_probe rc=0 BLIND / falsification --id STILL_HOLDS; NEG CONTROL sub-baseline fixture (5<18) still REGRESSED. zo-fleet-tools#105 merged (squash 87ee632b) + deployed to live _tools; overall staleness sweep zero-STALE. The named invariant-2 blindness is deliberately unchanged -- the falsification STILL HOLDS. Max sub -- no provider spend. Clean checkout off origin/main |
