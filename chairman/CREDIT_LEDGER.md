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
| claude-cloud-credit | ? | 2026-10-03 | 2026-10-08 | grant $250; spend never recorded (estimated $80–150 across 4 sessions to 2026-10-02) |
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
| 2026-10-03 | claude-cloud-credit | ? | session_01To4EdKyYczpEpTZv3Wa465 | store roles (GR-19..22), dead alert checks, credit ledger; three sub-agents |
