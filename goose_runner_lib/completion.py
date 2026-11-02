# goose_runner_lib/completion.py -- PART OF goose_runner.py, not a standalone module.
# code cleanup, fallback routing, result writing, artifact recording, gate chain, routing.
# goose_runner.py runs this file via _goose_runner_lib_exec("completion") INSIDE
# goose_runner's own namespace, at the exact position this code stood
# (original lines 1415-2401), so every name here is still
# goose_runner.<name> and __file__ is goose_runner.py.
# Do not import it directly; callers keep citing goose_runner.<name>.
# (chain rebase_godfiles S20, 2026-11-02; map: goose_runner_lib/README.md)
from __future__ import annotations

def _strip_code_fences(txt):
    """Strip markdown code fences the model may wrap a file in -- robust to
    ASYMMETRIC output. The old version only stripped when the response STARTED
    with a fence, so a code-first response with a stray trailing fence wrote the
    backticks into the .py and failed the Tier-0 py_compile gate (the 2026-06-23
    ghost_build cause). Now: prefer the FIRST well-formed fenced block; otherwise
    drop any standalone fence line so a leftover fence never reaches the gate."""
    import re
    s = txt.strip()
    _m = re.search(r"```[^\n]*\n(.*?)```", s, re.DOTALL)
    if _m:
        return _m.group(1).strip()
    _lines = [ln for ln in s.splitlines() if not ln.lstrip().startswith("```")]
    return chr(10).join(_lines).strip()


def call_minimax_fallback(directive):
    """Goose subprocess failed/unavailable -> route directive through the
    ladder shim (escalation.py, all 16 rungs). One HTTP call; the shim does
    MiniMax->Gemini->Gemma->Zo traversal internally."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    content = directive.get("content", directive.get("description", ""))
    log(f"Routing directive {directive_id} through ladder shim")
    try:
        resp = requests.post(SHIM_URL, json={
            # Shadow-corpus: ride directive_id on the model id ("<tier>#<id>") so the
            # shim can stamp it into the prompt ledger. This is goose_runner's own POST
            # (goose is not involved), so it is safe + always on; the shim strips it.
            "model": f"zo-ladder-nvidia#{directive_id}" if directive_id else "zo-ladder-nvidia",  # capable tool/coder window (was zo-ladder-v1=MiniMax rung0)
            "messages": [
                {"role": "system", "content": "You are an autonomous builder for zo-sentinel. Output only the completed code/results."},
                {"role": "user", "content": f"Complete this task: {content}"},
            ],
            "temperature": 0.2,
            "max_tokens": 16384,
        }, timeout=600)
        if resp.status_code == 200:
            txt = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            # PERSIST the generated code to the declared output. goose-developer
            # ghosted (no file on disk); the fallback historically returned the code
            # as TEXT and discarded it -> output_confirmed() always False -> a
            # GUARANTEED ghost for any directive goose itself couldn't build. Write it
            # so the EXISTING output_confirmed + Tier-0 py_compile gate in the main loop
            # can validate -- non-compiling/garbage output still re-ghosts (it is never
            # auto-completed). declared_output() is None for edit-class tasks
            # (wire_/rewire_/...), so those are skipped and keep trusting process success.
            try:
                _out = declared_output(directive)
                _code = _strip_code_fences(txt) if txt else ""
                if _out is not None and _code.strip():
                    _out.parent.mkdir(parents=True, exist_ok=True)
                    _out.write_text(_code, encoding="utf-8")
                    log(f"[fallback] wrote {len(_code)} bytes -> {_out.name} (Tier-0 gate will validate)")
            except Exception as _e:
                log(f"[fallback] write failed: {type(_e).__name__}: {_e}")
            return {"success": bool(txt), "result": txt, "fallback": "ladder_shim", "directive_id": directive_id}
        return {"success": False, "error": f"shim {resp.status_code}: {resp.text[:200]}", "directive_id": directive_id}
    except Exception as e:
        return {"success": False, "error": f"shim call failed: {type(e).__name__}: {e}", "directive_id": directive_id}

def route_to_coworker(directive):
    """Write high-complexity directive to coworker handoff queue.
    Syncthing syncs to tower; Claude Desktop / Coworker picks it up.
    """
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    handoff_dir = Path("/home/workspace/shared/outputs/coworker_queue")
    handoff_dir.mkdir(parents=True, exist_ok=True)

    handoff = {
        "directive_id": directive_id,
        "complexity": directive.get("complexity", "high"),
        "description": directive.get("description", ""),
        "content": directive.get("content", ""),
        "source": directive.get("source", ""),
        "handoff_at": get_utc_now(),
        "status": "pending_coworker",
        "instructions": (
            "High-complexity directive requiring Claude Code via Coworker.\n"
            "Codebase root: /home/workspace/zo_sentinel/\n"
            "Write outputs to: /home/workspace/zo_sentinel/ (in-place)\n"
            "Log results to: /home/workspace/logs/{directive_id}_coworker.log\n"
            "Mark done by writing: /home/workspace/zo_sentinel/directives/done/{directive_id}.json"
        ).format(directive_id=directive_id)
    }

    out = handoff_dir / f"{directive_id}.json"
    out.write_text(__import__("json").dumps(handoff, indent=2))
    log(f"[COWORKER] Handoff written: {out}")
    return out



# =============================================================================
# RESULT HANDLING
# =============================================================================

def write_result(directive_id, success, result_text="", error="", fallback_used=False):
    """Write execution result to shared outputs."""
    SHARED_OUTPUTS.mkdir(parents=True, exist_ok=True)
    output_file = SHARED_OUTPUTS / f"{directive_id}.result"
    
    result_data = {
        "directive_id": directive_id,
        "success": success,
        "timestamp": get_utc_now(),
        "result": result_text,
        "error": error,
        "fallback_used": fallback_used,
        "source": "goose_tier1" if not fallback_used else "minimax_fallback"
    }
    
    output_file.write_text(json.dumps(result_data, indent=2))
    log(f"Result written to {output_file}")
    return output_file

def mark_directive_completed(directive):
    """Mark directive as completed in mesh_events table."""
    directive_id = directive.get("directive_id") or directive.get("id", "unknown")
    
    try:
        # Update mesh_events table
        ws_write("mesh_events", {
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_COMPLETE",
            "payload": json.dumps({"directive_id": directive_id, "status": "completed",
                                   "completed_at": get_utc_now()}),
            "severity": "INFO",
            "created_at": get_utc_now()
        })
        Path(f"/home/workspace/zo_sentinel/directives/{directive_id}.done.json").write_text(
            json.dumps({"directive_id": directive_id, "completed_at": get_utc_now()}))
        log(f"Marked directive {directive_id} completed (mesh_events + done sentinel)")
    except Exception as e:
        log(f"Failed to mark directive completed: {e}")
    
    # Move file from pending to done if exists
    pending_file = PENDING_DIR / f"{directive_id}.json"
    if pending_file.exists():
        done_file = DONE_DIR / f"{directive_id}.json"
        try:
            content = pending_file.read_text()
            data = json.loads(content)
            data["status"] = "completed"
            data["completed_at"] = get_utc_now()
            data["completed_by"] = "goose_tier1"
            done_file.write_text(json.dumps(data, indent=2))
            pending_file.unlink()
            log(f"Moved {directive_id} to done directory")
        except Exception as e:
            log(f"Failed to move directive file: {e}")

def _emit_build_artifact_for(directive):
    """Emit the build_artifact mesh row for a confirmed creation build.

    RESTORES the emission builder_mcp.delegate_to_builder used to guarantee. #73
    moved goose to the developer extension (it writes files itself) and made the
    artifact depend on the model calling register_build -- which it does NOT do
    reliably, so confirmed builds landed on disk + .done but produced NO
    build_artifact -> the ingestor/governor/publisher were blind -> no PR. We know
    the file + tier here, so emit it directly, decoupled from the model.

    Edit-class directives (declared_output None -- wire/rewire/...) create no
    single new file, so no artifact (their multi-file diffs need a separate
    publisher path)."""
    out = declared_output(directive)
    if out is None or not out.is_file():
        return
    try:
        size = out.stat().st_size
        rel = (str(out.relative_to(PROJECT_DIR))
               if str(out).startswith(str(PROJECT_DIR)) else out.name)
        row = build_artifact_row(
            file=rel,
            content_bytes=size,
            context_type=str(directive.get("interface")
                             or directive.get("context_type") or "utility"),
            tier=tier_for_complexity(directive.get("complexity")),
            model=os.environ.get("GOOSE_MODEL", ""),
            backend="goose_developer",
            phase=str(directive.get("phase", "")),
            task=resolve_directive_id(directive),
        )
        row["created_at"] = get_utc_now()
        resp = ws_write("mesh_memory", row)
        if not (isinstance(resp, dict) and resp.get("ok")):
            # ws_write swallows errors and returns {"ok": False}; do NOT let a
            # failed write masquerade as "Emitted" (the silent-drop we chased:
            # build on disk + .done, but no build_artifact -> publisher blind).
            log(f"[artifact] ws_write returned {resp} for {rel} -- retrying once")
            resp = ws_write("mesh_memory", row)
        if isinstance(resp, dict) and resp.get("ok"):
            log(f"Emitted build_artifact for {rel} ({size} bytes) resp={resp}")
        else:
            log(f"[artifact] FAILED to persist build_artifact for {rel}: resp={resp} "
                f"-- publisher will NOT see this build")
    except Exception as e:
        log(f"Failed to emit build_artifact for "
            f"{directive.get('directive_id') or directive.get('id')}: {e}")


def _commit_build_for(directive):
    """The PRODUCER versions what it writes (RCA cross-cycle-2026-10, Fix 3).

    Until this, this file held no git code at all: a confirmed build landed in
    the runtime tree and a SEPARATE publisher, reading build_artifact rows
    behind a watermark, decided later whether it ever reached a PR. Six skip
    outcomes advanced the watermark with the file still on disk -- 462 of 504
    host services/active files (92%) were in no commit on 2026-09-27 (FU-555).

    Now the build is handed straight to ProducerCommit: a durable local outbox
    entry, the same pre-PR refusal rules, and a gated PR through the publish
    clone (never this tree). A refused or permanently-failed build is EVICTED
    from the runtime tree; a transient failure stays pending for the drain.
    Bookkeeping never fails the build: an exception here is logged and the
    entry (if written) stays pending.

    Edit-class directives (declared_output None) carry multi-file diffs and are
    not handled here -- the same scope the build_artifact row has always had."""
    out = declared_output(directive)
    if out is None or not out.is_file():
        return None
    try:
        if not str(out).startswith(str(PROJECT_DIR) + os.sep):
            log(f"[producer-commit] {out} is outside {PROJECT_DIR}; not versionable here")
            return None
        from zo_sentinel.publisher.__main__ import make_producer
        res = make_producer(str(PROJECT_DIR)).commit(
            str(out.relative_to(PROJECT_DIR)),
            task=resolve_directive_id(directive),
            phase=str(directive.get("phase", "")),
            interface=str(directive.get("interface") or directive.get("context_type") or ""),
            built_at=get_utc_now(),
            tier=tier_for_complexity(directive.get("complexity")))
        log(f"[producer-commit] {out.name}: {res}")
        return res
    except Exception as e:
        log(f"[producer-commit] FAILED for {out}: {e!r} -- outbox entry (if any) stays pending")
        return None


def _record_build_provenance(directive, success, smoke_result, attempt,
                             rescue_count, routed_model="", error=""):
    """Write one build_provenance row per ATTEMPT -- the failure-matrix substrate
    (Phase 4). build_provenance was defined-but-unwired, so the goose path recorded
    no per-attempt rung+outcome. Best-effort: a write_service hiccup must never fail
    the build (mirrors _emit_build_artifact_for). `routed_model` is the alias this
    build ACTUALLY routed to (build_env_for's GOOSE_MODEL) -- the rung the matrix
    learns on; we record THAT, not the daemon's ambient GOOSE_MODEL env (which is a
    single global and would make every row look identical). attempt drives the
    deterministic build_id."""
    try:
        out = declared_output(directive)
        output_path, output_bytes = "", 0
        if out is not None and out.is_file():
            output_path = (str(out.relative_to(PROJECT_DIR))
                           if str(out).startswith(str(PROJECT_DIR)) else out.name)
            output_bytes = out.stat().st_size
        row = build_provenance_row(
            directive_id=resolve_directive_id(directive),
            directive_type=str(directive.get("interface")
                               or directive.get("context_type") or "utility"),
            complexity=str(directive.get("complexity") or "unknown"),
            model=(routed_model or os.environ.get("GOOSE_MODEL", "")
                   or tier_for_complexity(directive.get("complexity"))),
            success=bool(success),
            smoke_result=smoke_result,
            attempt=attempt,
            rescue_count=rescue_count,
            output_path=output_path,
            output_bytes=output_bytes,
            error=str(error or ""),
        )
        resp = ws_write("build_provenance", row)
        if not (isinstance(resp, dict) and resp.get("ok")):
            log(f"[provenance] ws_write returned {resp} for {row['build_id']}")
    except Exception as e:
        log(f"[provenance] failed for "
            f"{directive.get('directive_id') or directive.get('id')}: {e}")


# Git commit of the state files is OFF by default: the host zo_sentinel clone is
# `git reset --hard origin/main` on every refresh (2026-05-31 ops note), so a
# local checkpoint commit there is futile/conflicting. The on-disk manifest is
# the resume source (it survives reset --hard because the state files are
# gitignored, and survives Modal reboot because the disk persists). Opt in with
# ZO_STATE_GIT_COMMIT=1 only off-host.
ZO_STATE_GIT_COMMIT = os.environ.get("ZO_STATE_GIT_COMMIT", "") not in ("", "0", "false", "False")


def _schema_prm_gate(directive, directive_id):
    """GraphifyKL pre-build PRM: deterministically reject a build whose declared output
    hallucinates the schema (unknown model columns/attrs, or inline mock models) vs the
    real app.models. On a violation record a lesson so the NEXT attempt is corrected
    (closed-loop feedback), then block completion. Best-effort: any internal error -> pass
    (never block a good build on a tooling hiccup)."""
    try:
        out = declared_output(directive)
        if out is None or not out.exists():
            return True
        # Schema PRM lints PYTHON modules against app.models. A non-.py output
        # (.html front-end view, .sql, ...) has no Python schema surface, and the
        # AST linter reports its non-Python syntax as a bogus "violation" that
        # ghosts the build (e.g. a dispute admin_disputes.html). Only gate .py.
        if out.suffix.lower() != ".py":
            return True
        import schema_kl
        try:
            kl = schema_kl.build_schema_kl()
        except Exception:
            try:
                kl = schema_kl.load_schema_kl(str(PROJECT_DIR / "graphify-out" / "schema_kl.json"))
            except Exception:
                return True
        # The SQL-string referent pass (the :8772 blind spot). THREE-STATE, and
        # the third state is reported, never folded into a pass:
        #   catalog present -> bus-bound SQL is checked against every plane
        #   catalog absent   -> the check is SKIPPED and the reason is LOGGED
        # A missing/stale catalog must not block: an empty catalog would mark
        # every table in every bus query as phantom and stop the whole fleet.
        # The staleness itself is not silent -- it is exactly what
        # referent-verify turns UNKNOWN-red on, and what the host refresher in
        # ops/zo_mesh/bus_catalog_refresh.sh exists to prevent.
        _sql_catalog, _cat_reason = schema_kl.load_referent_catalog()
        if _cat_reason:
            log(f"[schema-prm] {directive_id}: SQL referent pass SKIPPED -- {_cat_reason}")
        violations = schema_kl.lint_source(
            out.read_text(encoding="utf-8"), kl, sql_catalog=_sql_catalog)
        if not violations:
            return True
        obs = ("schema PRM rejected this build -- the module uses a schema not in app.models. "
               "Fix EXACTLY these before rebuilding: " + " | ".join(violations[:8]))
        log(f"[schema-prm] {directive_id}: BLOCKED -- {len(violations)} schema violation(s):")
        for _v in violations[:8]:
            log(f"    - {_v}")
        try:
            record_lesson(LESSONS_DIR, out.name, directive_id, "schema_prm", obs, severity=3)
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[schema-prm] {directive_id}: gate error (passing): {e}")
        return True


def _no_hollow_gate(directive, directive_id):
    """Reject a HOLLOW build BEFORE it is marked .done -- the earliest seam.

    A hollow scaffold (standalone FastAPI module with no app.db/app.models layer,
    or a mock/placeholder DB) compiles cleanly, so the Tier-0 syntax gate passes it
    and the build completes. The publisher (#1450) and CI then reject it -- correctly,
    but far too late: the build tokens are already spent, and the .done sentinel it
    left behind silently swallows any same-name reseed (which is why a rejected
    build has to come back renamed *_v2). Blocking HERE means the directive never
    completes, so it stays live and RETRIES -- and the lesson below puts the exact
    rejection into the next attempt's context, closing the loop the way the schema
    PRM does. Prose told the builder about this rule and it leaked anyway; the rule
    now lives in code (zo_sentinel.gates.hollow), shared verbatim with CI.

    Fail-open on any internal error, and live-flippable via the policy layer
    (builder.no_hollow_gate) -- a false positive here starves the builder, so the
    chairman must be able to switch it off without a redeploy. Publisher + CI remain
    as backstops either way.
    """
    try:
        try:
            from zo_sentinel import policy as _policy
            if not _policy.flag("builder.no_hollow_gate", directives_root=DIRECTIVES_PATH):
                return True
        except Exception:
            pass   # policy fault -> gate stays ON (fail-safe, not fail-open)
        out = declared_output(directive)
        if out is None or not out.exists():
            return True
        try:
            rel = str(out.relative_to(PROJECT_DIR)).replace("\\", "/")
        except Exception:
            rel = out.name
        reason = hollow_scaffold_scan(rel, out.read_text(encoding="utf-8"))
        if reason is None:
            return True
        obs = ("no-hollow gate rejected this build -- " + reason + ". The module must read the "
               "REAL data layer: import from app.db / app.models (get_session), mirror "
               "verdict_breakdown_api.py, and carry NO mock/placeholder/in-memory DB. "
               "Rebuild it against the real schema.")
        log(f"[no-hollow] {directive_id}: BLOCKED -- {reason} ({out.name})")
        try:
            record_lesson(LESSONS_DIR, out.name, directive_id, "no_hollow", obs, severity=3)
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[no-hollow] {directive_id}: gate error (passing): {e}")
        return True


# FU: deterministic self-test feedback-repair. _selftest_gate logs the RED stdout+stderr
# but returns only a bool, and _gate_chain depends on that bool signature. Rather than
# thread the error through the whole gate chain, stash the last RED output here keyed by
# directive_id; the build loop reads it to feed the model its own failure for ONE bounded
# repair re-run. Cleared on PASS so a stale error can never leak into a later repair.
_LAST_SELFTEST_RED: dict = {}


def _selftest_gate(directive, directive_id):
    """Run the module's __main__ self-test and require PASS before completing. Degrades to
    True (Tier-0 only) when there is no self-test or it fails purely on an environment/import
    error (so we never false-fail on missing deps), but a self-test that RUNS and does not
    print PASS is a real failure that BLOCKS completion. Closes the hole where the ladder-shim
    fallback stamped .done on a parse-only gate (e.g. server_compare_api hallucinating a
    'published_overall_risk' axis would now be caught instead of shipped)."""
    import subprocess, sys as _sys
    try:
        out = declared_output(directive)
    except Exception:
        return True
    if out is None or not out.exists():
        return True
    try:
        src = out.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return True
    # FU-031 harness repair (autonomous, deterministic, flag-gated). The dominant
    # self-test degradation is the model emitting WRONG-CASED app.models symbols
    # (e.g. MCPServerRegistry vs the real McpServerRegistry, or McpLlmAxisScores vs
    # McpLlmAxisScore) -> ImportError -> "degrade to Tier-0" below, i.e. presence
    # passing for correctness. Auto-correct the casing BEFORE the self-test runs so
    # it actually EXECUTES (it then still has to PASS on its own merits). Same shape
    # as _strip_code_fences: a deterministic pre-gate repair, no human in the loop,
    # false-positive-free (distinctive Mcp* names only). Kill with ZO_MODEL_CASING_AUTOFIX=0.
    import os as _oscas
    if _oscas.environ.get("ZO_MODEL_CASING_AUTOFIX", "1") != "0":
        try:
            from tools import model_import_linter as _mil
            _r = _mil.lint_file(str(out), _mil.build_map(_mil.canonical_models()), fix=True)
            if _r.get("fixed"):
                log(f"[casing-repair] {directive_id}: corrected {_r['drift']} before self-test")
                src = out.read_text(encoding="utf-8", errors="ignore")
        except Exception as _ce:
            log(f"[casing-repair] {directive_id}: skipped ({type(_ce).__name__}: {_ce})")
    if "__main__" not in src:
        return True
    try:
        import os as _os
        # in-memory SQLite + no Clerk -> the self-test imports app.db/app.models and runs
        # WITHOUT Postgres, DuckDB or Docker (lightweight, short-lived, one at a time).
        # FU-031 root cause. Python sets sys.path[0] to the SCRIPT'S OWN DIRECTORY,
        # and `cwd=` does NOT put PROJECT_DIR on sys.path. While builder output was
        # root-level files this was invisible (script dir == repo root, so `app.*`
        # resolved). The P2->P3 move to services/staged/<name>/*.py made sys.path[0]
        # the SERVICE directory, and every `from app.db import ...` began raising
        # ModuleNotFoundError -- which the branch below catches and degrades to
        # Tier-0, NON-BLOCKING. Result: 319 degradations vs 11 PASS (~96%), i.e. the
        # acceptance clause that makes a builder target self-verifying had not
        # executed since 2026-07-25T22:03Z.
        #
        # MUST PREPEND, never assign. This daemon inherits PYTHONPATH=/pkg/:/root/
        # from daemon_wrapper.sh (read from /proc/<pid>/environ); clobbering it would
        # break unrelated imports inside the self-test subprocess. Verified 3 ways:
        # cwd alone -> rc=1 regardless of PYTHONPATH; cwd + PYTHONPATH=<repo> -> PASS;
        # cwd + the real inherited /pkg/:/root/ -> reproduces production exactly.
        _env = {**_os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": "",
                "PYTHONPATH": str(PROJECT_DIR) + _os.pathsep + _os.environ.get("PYTHONPATH", "")}
        # FU-459: #2177 cured the `app.*` half of THIS loader (PYTHONPATH) and left
        # the RELATIVE-import half untouched. Running a file BY PATH can never give
        # it a parent package, so `from .logic import x` -- correct for a
        # services/staged/<name>/ package, and the exact form
        # promote_staged_to_active.py imports SUCCESSFULLY -- raised ImportError and
        # classify_selftest returned RED. Measured 2026-09-14 on the live runner log:
        # of the 14 services RED-ed for this in 24h, 6 (43%) imported CLEAN under the
        # runtime's own loader; the other 7 fail with `cannot import name` and stay
        # RED via the branch ABOVE the relative-import one, so no true positive is
        # lost. `python -m` still executes __main__, so the self-test contract holds.
        # Falls back to the path form for anything not under PROJECT_DIR.
        _selftest_argv = [_sys.executable, str(out)]
        _dotted = None
        try:
            _rel = _os.path.relpath(str(out), str(PROJECT_DIR))
            if not _rel.startswith(_os.pardir):
                _parts = _os.path.splitext(_rel)[0].split(_os.sep)
                if _parts and all(p.isidentifier() for p in _parts):
                    _dotted = ".".join(_parts)
        except Exception:
            _dotted = None
        if _dotted:
            _selftest_argv = [_sys.executable, "-m", _dotted]
        proc = subprocess.run(_selftest_argv, capture_output=True,
                              text=True, timeout=120, cwd=str(PROJECT_DIR), env=_env)
    except Exception as e:
        log(f"[selftest] {directive_id}: could not run ({type(e).__name__}: {e}) -- Tier-0 only")
        return True
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # FU-031/FU-159: three-state verdict. The old test was a substring match on an
    # exception NAME, so "the harness could not run" and "the module is broken" got
    # the same answer -- degrade. Measured post-#2177 (n=89): ALL 39 Tier-0
    # degradations were real module defects, and `No module named 'app.db'` had
    # already gone 295 -> 0. The UNKNOWN bucket was being used as a pass.
    from tools.selftest_verdict import (PASS as _V_PASS, RED as _V_RED,
                                        classify_selftest as _classify)
    _verdict, _reason = _classify(proc.returncode, combined, proc.stdout or "")
    if _verdict == _V_PASS:
        log(f"[selftest] {directive_id}: self-test PASS")
        _LAST_SELFTEST_RED.pop(directive_id, None)
        return True
    if _verdict == _V_RED:
        # Distinct string per meaning: one string for two meanings is why 39 events
        # read as one problem for nine days.
        log(f"[selftest] {directive_id}: self-test RED ({_reason}) -- blocking completion :: "
            + combined.strip()[-400:])
        # Stash the full RED output for the one-shot feedback-repair in the build loop.
        _LAST_SELFTEST_RED[directive_id] = combined.strip()
        return False
    log(f"[selftest] {directive_id}: self-test UNKNOWN ({_reason}) -- could not evaluate, "
        f"degrading to Tier-0 (not blocking) :: " + combined.strip()[-400:])
    return True


# Deterministic raw-SQL lint. The ORM idiom passes a select()/insert() CONSTRUCT to
# execute() and seeds via session.add(Model(...)); raw SQL passes a QUOTED STRING. That
# string boundary is what distinguishes the two cheaply and with near-zero false positives:
# `.execute(select(...))` never matches `execute(` + quote, but `.execute("INSERT ...")`
# does. VALUES(...) placeholder lists and text("...") are raw-only constructs.
_RAW_SQL_PATTERNS = [
    ("raw INSERT statement", re.compile(r"\bINSERT\s+INTO\b", re.I)),
    ("VALUES (...) placeholder list", re.compile(r"\bVALUES\s*\(", re.I)),
    ("execute()/executemany() on a SQL string literal",
     re.compile(r"\.execute(?:many)?\s*\(\s*[\"']", re.I)),
    ("sqlalchemy text() wrapping a SQL string", re.compile(r"\btext\s*\(\s*[\"']", re.I)),
    ("raw DBAPI cursor", re.compile(r"\.cursor\s*\(", re.I)),
]


def _raw_sql_lint(code):
    """Return a short hint naming the raw-SQL signals in `code`, or '' when it looks
    ORM-clean. Used only to SHARPEN the feedback-repair message -- never as a hard gate,
    so a false positive costs at most one needless ORM reminder, not a blocked build."""
    if not code:
        return ""
    hits = sorted({name for name, rx in _RAW_SQL_PATTERNS if rx.search(code)})
    return ("raw-SQL signals: " + "; ".join(hits)) if hits else ""


def _selftest_feedback(err, code):
    """Build the corrective block appended to the engine task for ONE bounded self-test
    repair re-run: quote the model's OWN self-test failure, and when the raw-SQL lint
    fires, point it hard at the ORM. Deterministic -- no model call here."""
    tail = (err or "").strip()[-1200:]
    lines = [
        "",
        "## PREVIOUS ATTEMPT FAILED ITS ACCEPTANCE SELF-TEST -- FIX EXACTLY THIS",
        "Your last output was written to disk and its __main__ self-test was run.",
        "It did NOT print PASS. The captured stdout+stderr tail was:",
        "```",
        tail or "(no output captured)",
        "```",
        "Correct the specific error shown above and re-emit the COMPLETE file.",
    ]
    lint = _raw_sql_lint(code)
    if lint:
        lines += [
            "",
            f"Static check of your last output -- {lint}. You wrote raw SQL; do NOT. "
            "Use the SQLAlchemy ORM only: import the model class from app.models, query "
            "with select()/session, and SEED rows via session.add(ModelName(col=value, "
            "...)) setting every required column -- never an INSERT/SELECT string, "
            "execute() on a string, or a hand-counted VALUES (?, ?, ...) placeholder list "
            "(that placeholder/column mismatch is exactly what failed).",
        ]
    return "\n".join(lines)


def _edit_diff_gate(directive, directive_id, pre_diff_state):
    """FU-015 ghost-edit guard: an edit-class directive (declared_output None)
    bypasses every file-existence gate -- output_confirmed trusts it, and the
    syntax/schema/hollow/self-test gates all no-op without a declared output --
    so a build that edited NOTHING used to complete and stamp .done (observed
    2026-07-14: a .done sentinel on 0 bytes of diff). Completion now requires
    the build workspace's git state (git status --porcelain + git diff HEAD)
    to be non-empty AND to have CHANGED from the fingerprint captured before
    the build started; a plain non-empty check alone is useless because the
    live workspace is permanently dirty. On an empty diff the directive routes
    to the existing ghost/failure path (bounded retries, then .failed) with the
    distinct marker "ghost-edit: empty diff". Fail-open (True) when git is
    unavailable or no pre-state was captured -- the guard must never block a
    real build on tooling absence. File-producing directives pass through
    untouched (output_confirmed owns those)."""
    try:
        if declared_output(directive) is not None:
            return True   # file-producing directive: file-based gates own it
        post = workspace_diff_state(str(PROJECT_DIR))
        if post is None:
            return True   # git unavailable -> fail open
        post_fp, post_dirty = post
        if not post_dirty:
            reason = "workspace has no uncommitted changes at all"
        elif pre_diff_state is not None and post_fp == pre_diff_state[0]:
            reason = "workspace diff unchanged since build start"
        else:
            return True
        log(f"[ghost-guard] {directive_id}: ghost-edit: empty diff -- {reason} "
            f"-- edit-class build produced no edit; not completing")
        try:
            record_lesson(LESSONS_DIR, directive_id, directive_id, "ghost_edit",
                          f"ghost-edit: empty diff -- {reason}. The build reported "
                          "success but modified no files in the workspace; the edit "
                          "must actually land on disk.", severity=3)
        except Exception:
            pass
        return False
    except Exception as e:
        log(f"[ghost-edit] {directive_id}: gate error (passing): {e}")
        return True


def _syntax_gate(directive, directive_id):
    """Tier-0 syntax gate on the declared output, recorded to the Default-FAIL
    manifest. Returns True when the file parses, or when there is no single
    declared .py output to gate (edit-class directives -- output_confirmed
    already validated those). Tier-1 import is deliberately NOT a hard gate:
    host-only deps (mcp/httpx/abs paths) false-FAIL it (workflow caveat), so it
    would block valid builds -- it is recorded as advisory in a later phase, not
    a completion blocker here. Never raises: a gate error must not regress a
    build that output_confirmed already proved landed on disk."""
    if run_gates is None:
        return True
    out = declared_output(directive)
    if out is None or not out.is_file():
        return True
    # Front-end outputs (.html) get a LENIENT structural gate, not py_compile:
    # parse with the stdlib HTML parser and require non-empty markup (the FE
    # analogue of the Tier-0 syntax gate / mirrors the CI front-end lane FE0).
    # Good HTML completes; empty/garbage HTML re-ghosts. Never false-fails: any
    # parser hiccup -> allow (output_confirmed already proved the file landed).
    if out.suffix.lower() in (".html", ".htm"):
        try:
            from html.parser import HTMLParser
            class _HG(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self.tags = 0
                    self.body = False
                def handle_starttag(self, tag, attrs):
                    self.tags += 1
                def handle_data(self, data):
                    if data.strip():
                        self.body = True
            hg = _HG()
            hg.feed(out.read_text(encoding="utf-8", errors="replace"))
            ok = hg.tags > 0 and hg.body
            if not ok:
                log(f"[eval-gate] {directive_id}: HTML gate FAIL on {out.name} "
                    f"(empty / no renderable markup) -- not completing")
            return ok
        except Exception as e:
            log(f"[eval-gate] {directive_id}: HTML gate error ({e}) -- allowing")
            return True
    if out.suffix != ".py":
        return True
    try:
        report = run_gates(out, [0])           # syntax only -- cheap, no false-fails
        sl.record_gate_result(directive_id, report)
        if not report.get("passed"):
            log(f"[eval-gate] {directive_id}: Tier-0 syntax FAIL on {out.name} -- not completing")
        return bool(report.get("passed"))
    except Exception as e:
        log(f"[eval-gate] {directive_id}: gate error ({e}) -- allowing (output_confirmed held)")
        return True


_BANDIT_READY = None  # tri-state cache: None=unchecked, True=available, False=unavailable


def _ensure_bandit():
    """One-time best-effort: make `python -m bandit` available (lazy pip install,
    cached so the build hot path pays the check once). Never raises."""
    global _BANDIT_READY
    if _BANDIT_READY is not None:
        return _BANDIT_READY
    import importlib.util
    if importlib.util.find_spec("bandit") is not None:
        _BANDIT_READY = True
        return True
    try:
        import subprocess, sys as _sys
        subprocess.run([_sys.executable, "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "bandit"],
                       capture_output=True, text=True, timeout=180)
        _BANDIT_READY = importlib.util.find_spec("bandit") is not None
    except Exception:
        _BANDIT_READY = False
    if not _BANDIT_READY:
        log("[security] bandit unavailable -- advisories disabled (functional builds unaffected)")
    return _BANDIT_READY


def _security_advisory(directive, directive_id):
    """SECONDARY (non-blocking) signal. AFTER a build has passed the hard gates and
    SHIPPED, run Bandit on the declared .py output and, on MEDIUM+ findings, record a
    LOW-severity security lesson under a SEPARATE subject ('<target>::security') so it
    teaches the NEXT build WITHOUT clobbering the functional/schema lesson and WITHOUT
    ever blocking a working, schema-bound build. Security is the 2nd objective:
    functionality + schema-binding + novelty come first. Best-effort; never raises."""
    try:
        out = declared_output(directive)
        if out is None or out.suffix.lower() != ".py" or not out.is_file():
            return
        if not _ensure_bandit():
            return
        import subprocess, sys as _sys, json as _json
        proc = subprocess.run(
            [_sys.executable, "-m", "bandit", "-q", "-f", "json", "-ll", str(out)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_DIR))
        try:
            data = _json.loads(proc.stdout or "{}")
        except Exception:
            return
        results = data.get("results", [])
        sec_subject = f"{out.name}::security"
        if not results:
            try:
                resolve_lessons(LESSONS_DIR, sec_subject)  # clean now -> close prior advisory
            except Exception:
                pass
            return
        tops = []
        for r in results[:4]:
            tops.append(f"{r.get('test_id','')} {r.get('issue_severity','')}: "
                        f"{str(r.get('issue_text',''))[:110]} (line {r.get('line_number','?')})")
        obs = ("Bandit flagged " + str(len(results)) + " MEDIUM+ security issue(s); when it does "
               "NOT compromise the working feature, prefer the safe form: " + " | ".join(tops))
        record_lesson(LESSONS_DIR, sec_subject, directive_id, "security_advisory", obs, severity=1)
        log(f"[security] {directive_id}: {len(results)} Bandit MEDIUM+ finding(s) -> advisory lesson (non-blocking)")
    except Exception as e:
        log(f"[security] {directive_id}: advisory skipped ({type(e).__name__}: {e})")


def _complete(directive, directive_id, result_text, fallback_used=False,
              routed_model=""):
    """A directive's declared output IS on disk AND passed the Tier-0 gate ->
    record + mark done for real."""
    write_result(directive_id, True, result_text, fallback_used=fallback_used)
    _emit_build_artifact_for(directive)   # the build record (ingestor/governor read it)
    mark_directive_completed(directive)
    # Closed-loop: a green build on this target auto-resolves any open lesson.
    try:
        _rout = declared_output(directive)
        resolve_lessons(LESSONS_DIR, _rout.name if _rout is not None else directive_id)
    except Exception:
        pass

    # SECONDARY objective (non-blocking): record a Bandit security advisory for the
    # NEXT build of this target. The feature already passed schema + self-test and
    # SHIPPED -- security teaches the next build, it never blocks functional work.
    try:
        _security_advisory(directive, directive_id)
    except Exception:
        pass
    # Provenance BEFORE clear_ghost so rescue_count reflects the retries it took.
    prior_ghosts = ghost_attempts(DIRECTIVES_PATH, directive_id)
    _record_build_provenance(directive, success=True, smoke_result="pass",
                             attempt=prior_ghosts + 1, rescue_count=prior_ghosts,
                             routed_model=routed_model)
    clear_ghost(DIRECTIVES_PATH, directive_id)
    # Default-FAIL contract: record the proven PASS + checkpoint for crash-resume.
    # Idempotent (record_pass overwrites; checkpoint refreshes the cursor).
    try:
        sl.record_pass(directive_id, f"output_confirmed + Tier-0 gate: {declared_output(directive)}")
        sl.checkpoint(directive_id, "complete")
        if ZO_STATE_GIT_COMMIT:
            sl.commit_checkpoint(f"chore(state): {directive_id} complete")
    except Exception as e:
        log(f"[loopback] checkpoint failed for {directive_id}: {e}")
    # RCA 2026-10 Fix 3: write + version, one owner. LAST, because publishing moves
    # the runtime copy aside (it re-enters through the merged ref) and everything
    # above still reads the file in place.
    _commit_build_for(directive)

def _mark_directive_failed(directive, directive_id, reason):
    """Give up after repeated ghost builds: write a .failed sentinel (so the
    runner stops churning and the generator stops re-proposing) + surface it.
    NOT a .done -- it never built; this makes the failure visible instead of
    masquerading as success the way ghost-completion did."""
    log(f"[ghost-guard] {directive_id}: GIVING UP after {MAX_GHOST_ATTEMPTS} ghost builds -- {reason}")
    # Closed-loop lesson: record WHY this gave up, keyed by the target it failed
    # on, so the architect read-gate (separate PR) can avoid re-proposing known-bad
    # work. File-based (zero DB load) + a best-effort mesh_memory mirror for history.
    # Wrapped: a lesson write must NEVER regress the failure path.
    try:
        _lout = declared_output(directive)
        _lsub = _lout.name if _lout is not None else directive_id
        _lstem = _lout.stem if _lout is not None else ""
        _lparts = [x for x in _lstem.split("_") if x]
        _lttype = ("doubled_path" if len(_lparts) >= 2 and _lparts[0] == _lparts[1]
                   else "ghost_no_output")
        record_lesson(LESSONS_DIR, _lsub, directive_id, _lttype, reason, severity=3)
        ws_write("mesh_memory", {
            "agent_id": "goose_tier1",
            "memory_type": "lesson_learned",
            "content": json.dumps({"subject_ref": _lsub, "directive_id": directive_id,
                                   "task_type": _lttype, "observation": reason,
                                   "severity": 3, "status": "open"}),
            "importance": 0.5,
            "created_at": get_utc_now(),
        })
    except Exception as _le:
        log(f"[lesson] emit failed for {directive_id}: {_le}")
    # Shared park primitive: in-repo + durable sentinel, and clears a stale .done.
    if not park_directive(directive_id, reason, get_utc_now(),
                          DIRECTIVES_PATH, DURABLE_QUARANTINE_DIR):
        log(f"Failed to write .failed sentinel for {directive_id}")
    try:
        ws_write("mesh_events", {
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_GHOST_FAILED",
            "payload": json.dumps({"directive_id": directive_id, "reason": reason}),
            "severity": "WARNING",
            "created_at": get_utc_now(),
        })
    except Exception:
        pass
    clear_ghost(DIRECTIVES_PATH, directive_id)
    pending_file = PENDING_DIR / f"{directive_id}.json"
    if pending_file.exists():
        try:
            pending_file.unlink()
        except Exception:
            pass

# --- Gate attribution (chairman review 2026-07-20, CofC ruling item 1) ------
# The completion gate chain has SEVEN distinct ways to reject a build, but every
# rejection used to be recorded in build_provenance with one hardcoded string:
# "ghost build: declared output_file was not produced". That string is a lie for
# six of the seven cases. On 2026-07-20, 17 of 37 builds were logged as ghosts
# under that message; the runtime log showed 12 of them were actually
# _edit_diff_gate rejections ("edit-class build produced no edit") -- the exact
# signal we need to explain 246 unmounted routers. The truth existed only in a
# tail-able log file, never in the queryable ledger, so every downstream
# analysis (census, ladder eval, FU triage) was reasoning off a single
# undifferentiated bucket.
#
# GATE_REASONS maps a gate key to the durable human-readable reason. The gate
# key is emitted into build_provenance.error as a "gate=<key>: <reason>" prefix.
# smoke_result stays "ghost" deliberately: existing consumers filter on it, and
# this change is meant to ADD attribution without moving anything downstream.
GATE_REASONS = {
    "engine_failed":  "engine reported failure (no build produced)",
    "output_missing": "declared output_file was not produced",
    "edit_diff":      "ghost-edit: empty diff -- edit-class build produced no edit",
    "syntax":         "declared output failed the syntax/parse gate",
    "schema_prm":     "declared output failed the schema-PRM gate",
    "no_hollow":      "declared output failed the no-hollow gate",
    "selftest":       "module __main__ self-test ran and did not PASS",
}
DEFAULT_GATE = "output_missing"


def gate_error_text(gate):
    """Durable ledger string for a gate rejection. Unknown gate -> honest
    'unattributed' rather than silently claiming the output was missing."""
    key = gate or DEFAULT_GATE
    reason = GATE_REASONS.get(key)
    if reason is None:
        return f"gate=unattributed: build rejected by an unrecognised gate ({key})"
    return f"gate={key}: {reason}"


def _write_raw_directive(directive, directive_id):
    """handler=="write_raw": the directive already CARRIES the exact bytes.

    CofC ruling 2026-09-10 (daily-chairman-review). The `handler` field was
    validated by zo_sentinel/mcp_servers/directive_mcp.py, by
    zo_sentinel/promoters/proposed_to_pending_promoter.py and by
    directive_validator.py -- and honoured by NO executor. Every directive,
    whatever its handler, reached run_goose_task(). So a write_raw scaffold
    directive handed an LLM the finished file as a task DESCRIPTION and asked it
    to produce that file; the model wrote its own chat instead. Measured
    2026-09-10 over all 361 open PRs: 68 carry a services/staged/<n>/service.toml
    that does not parse, first lines including `[TOOL_CALL]`, `---`, a python
    triple-quoted assignment, and "Now I'll create the router module:". 0 were
    unfetchable, so 68 is a direct count. Oldest 2026-07-31; none self-healed.

    Writing the bytes is both the cure and a saving: this class of directive
    stops spending an LLM invocation to reproduce what it was already given.
    The gate chain is UNCHANGED and still runs on the result -- no gate is
    added, none is bypassed (harness doctrine R7: recovery, not restriction).

    Returns True if the declared output landed on disk.
    """
    content = directive.get("content")
    if not isinstance(content, str) or not content:
        log(f"[write_raw] {directive_id}: no content -> falling through to engine")
        return False
    out = declared_output(directive)
    if out is None:
        log(f"[write_raw] {directive_id}: no declared output_file -> falling through")
        return False
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
    except Exception as exc:
        log(f"[write_raw] {directive_id}: write failed: {exc}")
        return False
    log(f"[write_raw] {directive_id}: wrote {len(content)} B verbatim -> {out} (no LLM)")
    return True


def _gate_chain(directive, directive_id, pre_diff_state, engine_ok):
    """Run the completion gate chain, returning (passed, failing_gate).

    Order and short-circuit semantics are IDENTICAL to the inline `and` chain
    this replaces -- the only new behaviour is that we remember WHICH gate said
    no. Note the asymmetry that made the old message so misleading: when
    declared_output(directive) is None (every wire_*/integrate_* edit-class
    directive), output_confirmed trusts it and the syntax/schema/hollow/selftest
    gates all no-op to True, so _edit_diff_gate is the ONLY gate that can reject
    such a build -- yet it was reported as a missing output_file."""
    if not engine_ok:
        return False, "engine_failed"
    if not output_confirmed(directive):
        return False, "output_missing"
    if not _edit_diff_gate(directive, directive_id, pre_diff_state):
        return False, "edit_diff"
    if not _syntax_gate(directive, directive_id):
        return False, "syntax"
    if not _schema_prm_gate(directive, directive_id):
        return False, "schema_prm"
    if not _no_hollow_gate(directive, directive_id):
        return False, "no_hollow"
    if not _selftest_gate(directive, directive_id):
        return False, "selftest"
    return True, None


def _ghost_or_fail(directive, directive_id, routed_model="", gate=None):
    """goose/fallback reported success but the build did not clear the gate
    chain. Count the ghost attempt; requeue for another try, or fail it once the
    cap is hit. Crucially we do NOT mark it .done -- that was the regression.

    `gate` names the gate that actually rejected the build (see GATE_REASONS);
    it is recorded in build_provenance so the ledger stops attributing every
    rejection to a missing output_file."""
    n = bump_ghost(DIRECTIVES_PATH, directive_id, get_utc_now())
    err = gate_error_text(gate)
    try:
        sl.record_fail(directive_id, f"ghost/gate fail ({gate or DEFAULT_GATE}), attempt {n}")
    except Exception:
        pass
    _record_build_provenance(directive, success=False, smoke_result="ghost",
                             attempt=n, rescue_count=n, routed_model=routed_model,
                             error=err)
    if n >= MAX_GHOST_ATTEMPTS:
        _mark_directive_failed(directive, directive_id, err)
        return
    write_result(directive_id, False, error=err)
    try:
        ws_write("mesh_events", {
            # mesh_events.agent_id is NOT NULL, and content/complexity/source/
            # retry_count are not columns -> the old row 500'd (constraint) + got
            # its keys dropped. Use the real schema (agent_id + payload).
            "agent_id": "goose_tier1",
            "event_type": "DIRECTIVE_GHOST_RETRY",
            "payload": json.dumps({"directive_id": directive_id, "retry_count": n}),
            "severity": "WARNING",
            "created_at": get_utc_now(),
        })
        log(f"[ghost-guard] {directive_id}: ghost attempt {n}/{MAX_GHOST_ATTEMPTS}, requeued for retry")
    except Exception as e:
        log(f"Failed to requeue {directive_id}: {e}")

def log_directive_routed(directive_id, source, complexity, method):
    """Log directive routing decision."""
    log(f"[ROUTE] directive_id={directive_id} complexity={complexity} source={source} method={method}")
    
    try:
        ws_write("audit_log", {
            "event_type": "goose_route",
            "actor": "goose_runner",
            "target_server_id": directive_id,
            "detail": json.dumps({
                "complexity": complexity,
                "source": source,
                "method": method
            }),
            "created_at": get_utc_now()
        })
    except Exception as e:
        log(f"Failed to log routing: {e}")
