# staging_drain — hand-off: what the cloud could not finish, and the exact commands that do

Written 2026-10-04 by the cloud session that built the chain (GR-23). One writer:
this file is hand-maintained; `STATUS.md`, `daily_line.txt`, `ledger.json` and
`directives/builder_exclusions.json` are derived by the tick and must not be hand-edited.

## What is true now (measured on main @ 60d32fd, 2026-10-04)

```
staging_drain: promoted 0 · superseded 22 · repairing 100 · retired 1259 · remaining 403 · wall: image size UNKNOWN budget (25 promotable api services import to 70 MiB in one process)
```

- 1,784 directories under `services/staged` (the "~426" count was a stale label).
- 1,259 have **no source** (1,259 `manifest_only`): `__init__.py` and/or `service.toml`
  only. `tools/service_decomposer.py` lands those two by `write_raw`; the engine's
  router/logic/contract land ~21% of the time (c122). Retired with that reason.
  Nothing deleted; a later census that finds source re-classifies them.
- 25 pass the real gate (`promote_staged_to_active.evaluate`), all `api`.
- 13 `worker` dirs (source entrypoint + every module imports) are promotable on the
  zo path, which has **no gate yet** beyond import + entrypoint.
- 465 repair targets; 100 have an open builder directive, 365 wait for later ticks
  (cap 100/tick). Classes: `lib_no_router` 155 (logic.py but no router — unfinished
  api scaffolds), `import_module_not_found` 103 (83 of them a router importing a
  `logic.py` that does not exist), `contract_missing` 90, `import_model_name` 50 (all
  family B: no referent in `app/models.py`), `contract_failed` 36,
  `import_undefined_name` 28, `import_other` 2, `hollow_member` 1.
- S2 harvest: **0 bytes**. All three mechanical scripts ran and refused every
  remaining site. The promoter's `casing_autofixed` field reported 165 sites and
  changed no file (`gate_changed_tree` = 0 in the census) — a GC-5 report of a
  mutation that did not happen.
- Image wall: importing all 25 promotable api routers into one interpreter reaches
  70 MiB max RSS (fastapi + sqlalchemy + `app.models` are ~67 MiB of that; the
  services add ~3 MiB together). The Fly machine's memory budget was not readable
  here; pass `--budget-mb` to turn the wall into `none` / `image size`.

## Reach from the cloud (checked, not assumed)

| Need | Result |
|---|---|
| Run the real gate on every staged dir | done here (census, 156 s on 4 cores) |
| `add_repo rob531/zo-fleet-tools` (one-pager section, chain driver layout) | **denied** by the session's permission classifier; nothing read or written there |
| Fly deploy / image boot test | no `flyctl`, no Docker in the container; not attempted |
| zo host, prod DB, `zo_lake` | not reachable from this container (write_service is intentionally not external); census written to a file instead of `zo_lake` |
| Tower `_chains/` driver | not in either repo; segments built as CLIs it can call |
| Spend | none (no vast, no paid API) |

## Commands that finish the chain (tower / zo), in order

1. **Merge the segments PR**, then on the tower's zo-sentinel checkout at `origin/main`:
   ```
   python tools/staging_drain/chain_tick.py --measure-wall --budget-mb <Fly machine MiB> --out-dir "D:\zo\Zocomputer Agents\_chains\staging_drain"
   ```
   Register it with ZoChainTick daily. Its `rc != 0` and a daily line with
   `remaining` not falling must reach the alert channel (GR-11). The ledger it writes
   under `chairman/staging_drain/` is committed by the tick's PR, like any lane output.

2. **One-pager line** (zo-fleet-tools, `chairman_daily_brief.py`): add one section that
   prints `chairman/staging_drain/daily_line.txt` from the zo-sentinel checkout (or the
   stdout of `python tools/staging_drain/report.py`). Nothing else from this chain goes
   in the brief.

3. **S4 batch 1** (draft PR, branch `staging-drain/s4-batch-1`: 10 api services moved
   staged→active, `app/_spine_generated.py` regenerated; `generate_spine --check` and
   `--strict` clean; 0 duplicate routes across the 69 active routers). To finish:
   ```
   git fetch origin staging-drain/s4-batch-1 && git worktree add D:\zo\_prod_dryrun origin/staging-drain/s4-batch-1
   cd D:\zo\_prod_dryrun && python tools/verify_deploy_candidate.py --json
   fly deploy --config fly.toml --remote-only            # the boot test IS the deploy of the candidate image
   curl -s https://<app>.fly.dev/spine/health            # ok:true, failures:[] and the 10 new import_paths present
   python tools/prod_drift_check.py                      # or the prod-drift-sentinel run for this deploy
   ```
   Green → merge, then record each of the 10 in `chairman/staging_drain/promotions.json`:
   ```json
   {"services": {"ghsa_feed_ingestor": {"at": "<utc>", "summary": "fly v<N>", "evidence": {"deploy": "v<N>", "spine_health": "<the json line>"}}}}
   ```
   That file is the only thing that turns `promotable` into `promoted` in the ledger.
   Red → revert the batch only (`git revert <merge>`), and paste the failure into the
   affected rows' repair directives (S5).
   Flag: `server_cve_exposure_api` declares `/api/exemplar/scores/{server_id}` — a route
   name mirrored from the exemplar. It passed the gate (real data layer, contract 200);
   a reviewer may still want it renamed before it is public.

4. **Before the first worker batch — prove zo reaches the prod DB** (one read, one
   idempotent write on a scratch table, through write_service, never DuckDB directly):
   ```
   python -c "from zobridge import ping, query; print(ping()); print(query('SELECT 1 AS ok'))"
   curl -s -X POST http://127.0.0.1:8772/write -H 'Content-Type: application/json' -d '{"table":"service_health","rows":{"service":"staging_drain_probe","last_heartbeat":"<utc>","status":"probe"},"wait":true}'
   ```
   If either fails, that path is the first repair; no worker is scheduled until it passes.
   Workers then go to the scripts-first scheduler as idempotent jobs (one writer per
   table), not daemons. There is no worker gate yet beyond import + entrypoint:
   writing one (`python -m services.staged.<name>.logic --once` exits 0 against the
   real bus) is the next S1 extension.

5. **Later ticks** drain the 365 deferred repair directives at 100/tick, and re-census
   anything the builder changes. The exclusion file (71 families: 59 active, 12
   superseded) is read by `proposed_to_pending_promoter` (fan-out → `.excluded`) and the
   architect floor the moment the segments PR is live on the host; confirm with the log
   line `family of ... is excluded by`.

## Findings filed on the way (each needs its own row or a note on GR-23)

- GC-5: `promote_staged_to_active.py` reports `casing_autofixed` from the linter's
  `drift` dict even when `lint_file` wrote nothing (`fixed=False`). 165 phantom fixes
  on this tree. Fix at the site: count only entries where `fixed` is true.
- GC-12: `service_decomposer` emits the manifest with `requires: [router.py]`, yet
  `scaffold_*_service_toml` PRs keep landing before any router exists (15 of the last
  400 commits; 0 router/logic/contract scaffolds in the same window). Either
  `requires` is not honoured by the live goose_runner or the host tree differs from
  the repo (`tools/staged_repo_reconcile.py`). This is the emitter of the 1,259.
- `tests/test_promotion_blocks_unshippable_import_path.py::test_an_uncopied_tree_is_still_not_shipped`
  is red on `origin/main` (the Dockerfile now has `COPY tools /srv/tools`, so `tools.*`
  is shipped and the test's negative control no longer exists). Not this chain's.
- The promoter computes active routes once per run; a batch of N can promote two
  services whose routes collide with each other. Checked by hand for batch 1 (0 dupes);
  a post-batch duplicate-route check belongs in the promoter.
