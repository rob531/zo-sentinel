# goose_runner_lib -- goose_runner.py split behind its unchanged name (2026-11-02)

Chain `rebase_godfiles` S20. These files are NOT modules: goose_runner.py executes
each one in its own namespace at the position the code originally stood. Keep
calling `goose_runner.<name>` / `python goose_runner.py ...`; edit the code in the
file named below.

| file | original goose_runner.py lines | what | names |
|---|---|---|---|
| core.py | 107-472 | utility functions, heartbeat, ws I/O, directive management, goose eligibility check | log, get_utc_now, send_heartbeat, heartbeat_loop, check_single_instance, remove_pid_file, signal_handler, ensure_directories, ws_query, ws_write, failure_matrix_cached, _parse_directive, prune_done_pending, topup_quarantine, park_orphan_chains_safe, load_directives_from_mesh, is_goose_eligible |
| context.py | 473-1414 | task spec, build context, model roster, SOA/recipe helpers, task execution | write_task_spec, check_goose_installed, _graph_context, _lessons_context, _model_roster, _data_access_context, _select_recipe, _soa_int_env, _sanitise_service_name, _soa_service_name, _fmt_cols, _soa_schema_excerpt, _schema_kl_cached, _schema_ground_context, _bus_table_context, _reads_context, _engine_task, _soa_service_spec, _soa_day_key, _soa_state_read, _soa_state_write, recipe_counter, _soa_budget_left, soa_params_or_none, recipe_required_params, run_goose_task |
| completion.py | 1415-2401 | code cleanup, fallback routing, result writing, artifact recording, gate chain, routing | _strip_code_fences, call_minimax_fallback, route_to_coworker, write_result, mark_directive_completed, _emit_build_artifact_for, _commit_build_for, _record_build_provenance, _schema_prm_gate, _no_hollow_gate, _selftest_gate, _raw_sql_lint, _selftest_feedback, _edit_diff_gate, _syntax_gate, _ensure_bandit, _security_advisory, _complete, _mark_directive_failed, gate_error_text, _write_raw_directive, _gate_chain, _ghost_or_fail, log_directive_routed |
| runner.py | 2402-2718 | main polling loop | run |
