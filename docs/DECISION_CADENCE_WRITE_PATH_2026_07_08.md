# Council ruling — cadence write path (2026-07-08)

*Council of 3 (PRO / CONTRA / HISTO) + FATHER, convened by the chairman's
directive to resolve the deferred `perspective_snapshot_daemon` +
`ask_corpus_drift_guard` builds. All three chairs independently picked
design B; FATHER's ruling is binding.*

## The question

Both jobs were deferred (2026-07-06) because "daemons are read-only by
decree; writes go through the queued write_service" — but they must write
`perspective_snapshots` / `perspective_events` / `ask_corpus_index` rows in
the **prod Fly Postgres** plane.

## DOCTRINE (canonical — this question does not recur)

**Externally-triggered admin endpoints are NOT "daemons" under the decree.**
The read-only decree governs the **factory DuckDB plane** (write_service
:8772 single-writer queue). A request-scoped, externally-triggered admin
endpoint writing to the prod PG plane the app already owns is an
**application write path**. The banned artifact is the *long-lived
in-process loop with unmanaged lifecycle* (the orphaned/duplicate-daemon
scar tissue), not *scheduled writes*.

## Ruling (design B, with CONTRA's safeguards folded in as MUSTs)

1. Bulk admin endpoints on the prod app (reusing `take_snapshot` /
   `api_reindex` internals), externally triggered.
2. Primary trigger = **tower scheduled task** (the proven pattern: nightly
   465k-row PG backup, deploy-runtime, vast audit). GH Actions cron only as
   documented failover, never dual-live.
3. Invocation key from **AgentVault only** (tower) / Fly secret (app);
   never plaintext in YAML or task definitions.
4. **Job-status row per run** for both jobs (`cadence_job_runs`).
5. The ~66k reindex is **enqueue-then-poll**, never synchronous in-request.
6. **Advisory lock** at job start, bounded hold, released in finally,
   fail closed if unacquired.
7. **Missed-cadence alerting** surfaced where the freshness gate reads
   (`GET /api/admin/cadence/health`, overdue ⇒ alert), + cost ceilings.
8. First-week operation is gated verification: rows landing end-to-end,
   not config merged ("switch had no lever" lesson).
9. MUST NOT: design A (in-prod lifespan scheduler) or C (factory→prod
   write_service bridge).

## Acceptance gates

- **G1**: first scheduled run of each job lands rows in prod PG, verified by
  query.
- **G2**: forced-fail run shows `status=failed` and closes safely (lock
  released, ceiling honored).
- **G3**: a missed run raises the health alert within one cadence period.

## Implementation

`cadence_admin_api.py` (+ `cadence_job_runs` migration 0008, CI gate
`tests/test_cadence_admin.py`). Supersedes the `perspective_snapshot_daemon`
and `ask_corpus_drift_guard` directive candidates in PRODUCT_SPEC — the
architect must NOT re-propose those as daemons.
