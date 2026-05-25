# 2026-04-28 - Smoke Gate False Pass on evidence_density_enrichment

A built-and-smoke-passed file shipped two security/correctness defects.

## What happened

15:17 UTC: builder picked up gen_cto_evidence_density_enrichment.json
         (one of three CTO-injected directives breaking the empty-queue state).
15:18 UTC: MiniMax generated a 7211-byte file. Smoke gate passed it.
15:35 UTC: CTO post-build read flagged three defects in the v1 output:
           1. Two `eval(resp.read().decode('utf-8'))` calls -- RCE if write_service
              ever returns hostile data.
           2. `str(payload).encode('utf-8')` for POST bodies -- serializes Python
              repr instead of JSON. Every write would 500. Bare-except swallows it.
              Daemon would heartbeat forever doing nothing useful.
           3. SQL string interpolation (server_id is a hash so practically safe,
              but bad pattern that could be templated badly later).
           Plus contract violation: directive asked for a pure function; output
           was a daemon with file/network/DB I/O.
15:36 UTC: live file replaced with v1.1 -- pure function only, drops all unsafe
           code. Original preserved at evidence_density_enrichment.bak.20260428_153520.

The other two directives in the same batch (registry_breadth_enrichment,
coverage_gap_reporter) came back clean. Same model, same prompt context,
different output. LLM variability.

## Why the smoke gate missed it

zo_sentinel_builder.py's smoke_test_file() validates: file imports cleanly,
no obvious syntax errors, uvicorn-style daemons start without crashing.

It does NOT check:
- eval/exec usage on untrusted inputs
- requests.post(..., json=X) vs requests.post(..., data=str(X).encode())
- contract conformance (function signature, side-effect prohibitions)
- whether the file matches the directive's stated shape (pure function vs daemon)

## Hardening proposal (file as a future builder directive)

Add a `static_safety_lint()` step to smoke that:
1. Greps for `eval(` and `exec(` in the generated code; if either is present
   AND the function takes any external input (HTTP, file read, DB row),
   FAIL the smoke and write to GENERATION_FAILURES.md with reason 'eval_with_external_input'.
2. For any `requests.post(...)` call, confirm the call uses `json=` kwarg
   when the directive description mentioned 'JSON' or 'write_service' --
   otherwise FAIL with 'wrong_serialization_for_write_service'.
3. Compare directive.handler+description shape against the generated code:
   - If description includes 'PURE FUNCTION' or 'compute_score' contract,
     reject any code that has a `while True:` loop, opens sockets, or imports
     `requests`, `urllib.request`, `subprocess`, `signal`, or `threading`.
   - Conversely, if description includes 'Long-running daemon', allow those.
4. Re-run the smoke against the v1 file as a regression test --
   it should fail under the new rules.

Queue this as a directive: `harden_smoke_gate_static_safety_v1` ->
`smoke_static_safety.py`. Validator integration via build_task_generate_file's
smoke_test_file() hook. Idempotent: rerunning the lint is safe.

## Lessons for future CTO sessions

1. ALWAYS post-build read the actual output, not just the smoke verdict.
2. The LLM will sometimes silently ignore the contract. Variability is
   inherent; the gate must catch it, not the human.
3. Files with `if __name__ == '__main__': run()` that aren't supposed to be
   daemons are a common shape-violation tell.
4. The .bak files from zo_write_file's auto-backup are a free safety net --
   we don't need to fear in-place rewrites of generated code.

## Status: contained

- v1 was never started (not in supervisord; the builder never daemonizes
  outputs). Risk window: zero.
- v1.1 is in place and matches the contract.
- Backup of v1 retained for forensics at
  /home/workspace/zo_sentinel/evidence_density_enrichment.bak.20260428_153520.
- Smoke-gate hardening tracked above; not yet a directive.