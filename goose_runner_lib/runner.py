# goose_runner_lib/runner.py -- PART OF goose_runner.py, not a standalone module.
# main polling loop.
# goose_runner.py runs this file via _goose_runner_lib_exec("runner") INSIDE
# goose_runner's own namespace, at the exact position this code stood
# (original lines 2402-2718), so every name here is still
# goose_runner.<name> and __file__ is goose_runner.py.
# Do not import it directly; callers keep citing goose_runner.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: goose_runner_lib/README.md)
from __future__ import annotations

# =============================================================================
# MAIN LOOP
# =============================================================================

def run():

    # Route Goose through the ladder shim (escalation.py, 16 rungs).
    # Shim holds no creds; escalation.py reads vault-hydrated keys from env.
    import os
    os.environ['GOOSE_PROVIDER']  = 'openai'
    os.environ['GOOSE_MODEL']     = 'MiniMax-Text-01'
    os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:8796/v1'
    os.environ['OPENAI_API_KEY']  = 'dummy_key_for_shim'  # bypasses keyring check
    """Main daemon loop."""
    log(f"=== Goose Runner starting ===")
    log(f"Service: {SERVICE_NAME}, Port: {SERVICE_PORT}")
    
    ensure_directories()
    check_single_instance()

    # Default-FAIL loop-back: surface the last checkpoint so a crash-restarted
    # container shows where it left off (file-based; survives reboot + refresh).
    try:
        cursor = sl.resume()
        if cursor:
            log(f"[loopback] resume cursor: last_step={cursor.get('last_step')} at={cursor.get('at')}")
    except Exception as e:
        log(f"[loopback] resume read failed: {e}")

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start heartbeat thread
    heartbeat_thread = Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    log("Heartbeat thread started")
    
    # Check Goose installation
    goose_installed = check_goose_installed()
    if goose_installed:
        log("Goose CLI verified")
    else:
        log("Goose CLI not available - will use fallback on first directive")
    
    cycle_count = 0
    
    while True:
        try:
            cycle_count += 1
            send_heartbeat()
            log(f"=== Cycle {cycle_count} ===")
            
            # Load eligible directives
            directives = load_directives_from_mesh()
            
            if not directives:
                log("No eligible directives found")
            else:
                log(f"Processing {len(directives)} directives")
            
            for directive in directives:
                directive_id = directive.get("directive_id") or directive.get("id", "unknown")
                complexity = directive.get("complexity", "unknown")
                source = directive.get("source", "unknown")
                
                if not is_goose_eligible(directive):
                    log(f"Skipping non-eligible directive: {directive_id} (complexity={complexity})")
                    continue

                # High complexity -> coworker handoff, not Goose
                if complexity == "high":
                    log(f"[COWORKER] Routing high-complexity directive: {directive_id}")
                    route_to_coworker(directive)
                    mark_directive_completed(directive)
                    continue
                
                # c122 2026-09-21: a directive may declare `requires` -- files
                # that must ALREADY exist for its own output to be TRUE rather
                # than merely present. tools/service_decomposer.py stamps it on
                # service.toml, whose import_path names a router.py that a LATER,
                # engine-dependent directive is supposed to write. Measured on
                # origin/main @ 289f1f1ec by running the promoter itself: 1226 of
                # 1476 staged HOLDs are the one reason "router.py exposes no
                # router", because the manifest lands deterministically and the
                # router does not.
                #
                # DEFER -- do not fall through. Letting this reach the engine is
                # precisely the 68-unparseable-service.toml bug the write_raw
                # dispatch was added to stop. Deferring costs no LLM call, ghosts
                # nothing, parks nothing, and leaves no FAIL row in the loopback
                # manifest (this runs BEFORE sl.init_manifest deliberately); the
                # directive lands by itself on a later pass once the router
                # exists. Inert for every directive that declares no `requires`.
                _unmet = unmet_requires(directive, str(PROJECT_DIR))
                if _unmet:
                    log(f"[requires] {directive_id}: DEFERRED, still pending -- "
                        f"absent: {', '.join(_unmet)}")
                    continue

                log(f"Processing directive: {directive_id} (complexity={complexity})")
                log_directive_routed(directive_id, source, complexity, "goose_tier1")
                
                # Write task spec
                write_task_spec(directive)
                sl.init_manifest([directive_id])   # Default-FAIL until proven (idempotent: setdefault)

                # Build via Goose, falling back to MiniMax. CRITICAL: a directive
                # is "done" ONLY when its declared output_file actually lands on
                # disk (output_confirmed) AND the file parses (Tier-0 gate). Both
                # goose and the fallback can report success WITHOUT producing the
                # file, or produce a syntactically broken one -- stamping that
                # .done was the ghost-completion regression that burned directives.
                # Phase 5: route by the prior ghost-retry count so a re-asserted
                # directive escalates up the ladder (no-op unless ZO_ESCALATE set).
                # Capture the routed alias here so build_provenance records the rung
                # the build ACTUALLY used (not the daemon's ambient GOOSE_MODEL).
                _attempt = ghost_attempts(DIRECTIVES_PATH, directive_id)
                # route by the recipe we will actually run (inferred recipe was not stamped -> app builds fell to MiniMax)
                directive["recipe"] = _select_recipe(directive) or directive.get("recipe", "")
                _routed_env = build_env_for(directive, attempt=_attempt,
                                            matrix_rows=failure_matrix_cached())
                _routed_model = _routed_env.get("GOOSE_MODEL", "")
                # Spine gate: for app-surface builds (webapp/service recipes only), classify the
                # directive before spend. route (existing mount) + new_service (legitimate spine
                # GROWTH -- a structured build of a not-yet-mounted service) both PROCEED and are
                # never parked; only reject (orphan / no service structure) is gated. ADVISORY by
                # default; ZO_SPINE_GATE_ENFORCE=1 parks a reject. Scoped + fail-open: no non-app
                # directive is ever blocked, and any router error lets the build proceed.
                try:
                    if _spine_classify and directive.get("recipe") in _SPINE_APP_RECIPES:
                        _goal = " ".join(str(directive.get(k, "")) for k in
                                         ("title", "description", "content", "task"))
                        _decision, _target = _spine_classify(_goal, directive_id)
                        log(f"[SPINE] directive_id={directive_id} recipe={directive.get('recipe')} -> {_decision}:{_target}")
                        if _decision == "reject":
                            if os.environ.get("ZO_SPINE_GATE_ENFORCE") == "1":
                                park_directive(directive_id,
                                               "spine: REJECT_UNSPINED (orphan / no mounted route, not a service build)",
                                               get_utc_now(), DIRECTIVES_PATH, DURABLE_QUARANTINE_DIR)
                                log(f"[SPINE] PARKED (enforce) orphan directive: {directive_id}")
                                continue
                            log(f"[SPINE] ADVISORY would-reject orphan (set ZO_SPINE_GATE_ENFORCE=1 to park): {directive_id}")
                except Exception as _se:
                    log(f"[SPINE] gate skipped (fail-open): {_se}")
                produced = False
                # Compose the fully-grounded task ONCE, for BOTH engines. The
                # engine-fallback fix (2026-07-02): the deterministic engine used
                # to get only the raw content while goose got graph + lessons +
                # data-access grounding -- so "both engines failed" ghosts were
                # often the second engine failing for lack of the context the
                # first already had.
                _task = directive.get("content", "")
                _gctx = _graph_context(directive)   # Phase 3: fold graph structure into the task
                if _gctx:
                    _task = f"{_task}\n\n{_gctx}"
                _lctx = _lessons_context(directive)   # closed-loop READER: prior failures on this target
                if _lctx:
                    _task = f"{_task}\n\n{_lctx}"
                _dctx = _data_access_context(directive)   # proactive DB-access grounding (pre-empt CSV hallucination)
                if _dctx:
                    _task = f"{_task}\n\n{_dctx}"
                # FU-015: fingerprint the workspace's git state BEFORE the build
                # so _edit_diff_gate can prove an edit-class build actually changed
                # something (None -> that gate fails open).
                _pre_diff = workspace_diff_state(str(PROJECT_DIR))
                # Which gate rejected the build. Carried to _ghost_or_fail so the
                # ledger records the real reason instead of a blanket
                # "output_file was not produced" (chairman review 2026-07-20).
                _failed_gate = None
                # CofC 2026-09-10: handler dispatch, which this executor never
                # had. A write_raw directive carries its finished bytes; write
                # them rather than asking a model to re-derive them. Anything
                # that is not a satisfiable write_raw falls through to exactly
                # the path it took before.
                _raw_done = False
                if directive.get("handler") == "write_raw":
                    if _write_raw_directive(directive, directive_id):
                        _ok, _failed_gate = _gate_chain(directive, directive_id,
                                                        _pre_diff, True)
                        if _ok:
                            _complete(directive, directive_id,
                                      "write_raw: content written verbatim",
                                      routed_model="write_raw")
                            # 2026-09-11 daily-chairman-review: without this the
                            # engine fallback below (`if not produced:`) runs anyway
                            # and the model writes its chat over the bytes write_raw
                            # just wrote. Measured: 326 B verbatim at 21:43:45Z ->
                            # 187 B of prose at 21:44:19Z -> PR #4958 born RED.
                            # SUCCESS ONLY: a rejected gate chain still falls through.
                            produced = True
                        else:
                            _ghost_or_fail(directive, directive_id,
                                           routed_model="write_raw",
                                           gate=_failed_gate)
                        _raw_done = True
                if _raw_done:
                    pass
                elif goose_installed:
                    result = run_goose_task(directive_id, _task, _routed_env, recipe=_select_recipe(directive), directive_obj=directive)
                    _ok, _failed_gate = _gate_chain(directive, directive_id, _pre_diff,
                                                    bool(result.get("success")))
                    if _ok:
                        _complete(directive, directive_id, result.get("stdout", ""),
                                  routed_model=_routed_model)
                        produced = True
                    elif result.get("success"):
                        log(f"[ghost-guard] {directive_id}: goose reported success but the build "
                            f"was rejected by gate '{_failed_gate}' "
                            f"({GATE_REASONS.get(_failed_gate, 'unattributed')}); "
                            f"declared output = {declared_output(directive)} -- not completing")
                    else:
                        log(f"Goose failed for {directive_id}: {result.get('error')}")

                if not produced:
                    # Engine fallback, first-class (gated, fail-open): the
                    # grounded deterministic engine with per-attempt capable-rung
                    # escalation and ONE bounded repair round. Falls back to the
                    # legacy bare-prompt call_minimax_fallback when the gate is
                    # off or the module errors -- behavior is IDENTICAL to before
                    # unless directives/.engine_build_on is "1". Completion
                    # authority stays with the gate chain below either way.
                    fallback_result = None
                    try:
                        from zo_sentinel import engine_build as _engine
                        if _engine.enabled(DIRECTIVES_PATH):
                            fallback_result = _engine.build_with_engine(
                                directive, _engine_task(_task, directive, log),
                                attempt=_attempt, log=log)
                            if fallback_result.get("model"):
                                _routed_model = f"engine:{fallback_result['model']}"
                    except Exception as _ee:
                        log(f"[engine] unavailable ({type(_ee).__name__}: {_ee}) "
                            f"-- fail-open to legacy fallback")
                        fallback_result = None
                    if fallback_result is None:
                        fallback_result = call_minimax_fallback(directive)
                    _ok, _failed_gate = _gate_chain(directive, directive_id, _pre_diff,
                                                    bool(fallback_result.get("success")))
                    if _ok:
                        _complete(directive, directive_id,
                                  fallback_result.get("result", ""), fallback_used=True,
                                  routed_model=_routed_model)
                        produced = True

                if (not produced and _failed_gate == "selftest"
                        and os.environ.get("ZO_SELFTEST_REPAIR", "1") != "0"):
                    # Deterministic self-test feedback-repair (ONE bounded re-run). Both
                    # prompt-level fixes -- exemplar inlining (#11750) and the ORM-mandate
                    # schema header (#11751) -- left logic-leaf builds RED because the cheap
                    # rung still improvised a raw-SQL seed INSERT (placeholder/column
                    # mismatch) against the 15-col scoring table. A BLIND ghost retry just
                    # re-rolls the same dice. This re-runs the engine ONCE with the model's
                    # own quoted self-test failure (+ a deterministic raw-SQL lint) so the
                    # repair is informed, not random; it REPLACES a blind retry rather than
                    # adding a round, so the cost ceiling is unchanged. The gate chain still
                    # owns completion. Kill with ZO_SELFTEST_REPAIR=0.
                    try:
                        _err = _LAST_SELFTEST_RED.get(directive_id, "")
                        _rout = declared_output(directive)
                        _code = (_rout.read_text(encoding="utf-8", errors="ignore")
                                 if (_rout and _rout.exists()) else "")
                        _lint = _raw_sql_lint(_code)
                        log(f"[selftest-repair] {directive_id}: one bounded re-run with "
                            f"quoted self-test error"
                            + (f" (+{_lint})" if _lint else ""))
                        _repair_task = (_engine_task(_task, directive, log)
                                        + _selftest_feedback(_err, _code))
                        _repair_result = None
                        try:
                            from zo_sentinel import engine_build as _engine
                            if _engine.enabled(DIRECTIVES_PATH):
                                _repair_result = _engine.build_with_engine(
                                    directive, _repair_task, attempt=_attempt, log=log)
                                if _repair_result.get("model"):
                                    _routed_model = f"engine:{_repair_result['model']}:repair"
                        except Exception as _ree:
                            log(f"[selftest-repair] {directive_id}: engine unavailable "
                                f"({type(_ree).__name__}: {_ree}) -- legacy fallback")
                            _repair_result = None
                        if _repair_result is None:
                            _repair_result = call_minimax_fallback(directive)
                        _ok, _failed_gate = _gate_chain(directive, directive_id, _pre_diff,
                                                        bool(_repair_result.get("success")))
                        if _ok:
                            log(f"[selftest-repair] {directive_id}: repair cleared the gate chain")
                            _complete(directive, directive_id,
                                      _repair_result.get("result", ""), fallback_used=True,
                                      routed_model=_routed_model)
                            produced = True
                        else:
                            log(f"[selftest-repair] {directive_id}: still rejected by gate "
                                f"'{_failed_gate}' -- routing to ghost/retry")
                    except Exception as _rex:
                        log(f"[selftest-repair] {directive_id}: repair error "
                            f"({type(_rex).__name__}: {_rex})")

                if not produced:
                    # Neither path cleared the gate chain -> ghost build. Retry
                    # (bounded) instead of stamping a bogus .done. _failed_gate
                    # carries the LAST gate that rejected, so the ledger records
                    # the real reason.
                    _ghost_or_fail(directive, directive_id, routed_model=_routed_model,
                                   gate=_failed_gate)

                # Small delay between directives
                time.sleep(2)
            
            log(f"Cycle {cycle_count} complete, sleeping {POLL_SECS}s")
            time.sleep(POLL_SECS)
            
        except Exception as e:
            log(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(POLL_SECS)
