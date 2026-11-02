# weekly_rescore_lib -- weekly_rescore.py split behind its unchanged name (2026-11-02)

Chain `rebase_godfiles` S20. These files are NOT modules: weekly_rescore.py executes
each one in its own namespace at the position the code originally stood. Keep
calling `weekly_rescore.<name>` / `python weekly_rescore.py ...`; edit the code in
the file named below.

| file | original weekly_rescore.py lines | what | names |
|---|---|---|---|
| core.py | 129-509 | utilities, Run dataclass, run state tracking and lifecycle management | utcnow, log, ledger, secret, _driver_provenance, Run, _terminally_finished, open_run, _is_abort_event, _state_abandoned, _state_terminal, _parse_ts, open_runs, check_open_runs |
| preflight.py | 514-823 | Fly proxy setup, PostgreSQL connection, freshness checks, preflight phase | ensure_proxy, pg_conn, freshness, freshness_safe, ph_preflight, _read_changed_priority, residue_verdict, snap_refresh_to_cohort, cohort_trust |
| phases.py | 824-1507 | export, bundle, fire, vast instance lifecycle, watch and collect phases | ph_export, adapter_blob_size, ph_bundle, ph_fire, _results_state, _destroy, _eff_cost_cap, _eff_deadline, _eff_cap_usd, _vast_credit, _read_launch_credit, _spent_since_launch, _billed_dph, _instance_probe, _pod_progress, _watch_basis, _pull_instance_logs, ph_watch_collect |
| complete.py | 1508-1979 | import, backfill, postcheck phases, and main entrypoint | ph_import, ph_backfill, refresh_yield, zero_yield_streak, ph_postcheck, main |
