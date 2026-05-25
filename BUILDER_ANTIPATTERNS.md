# Builder Antipatterns -- v0.1 (seeded 2026-04-26)

This file is the canonical, builder-readable index of failure patterns the
generation pipeline must avoid. It is consumed by `sentinel_directive_generator.py`
before directive emission, and by `zo_sentinel_builder.py` rescue_smoke gates
before deploy.

Seeded from `mesh_events.event_type='build_failed'` between 2026-04-20 and
2026-04-26 (11 failures observed). Each pattern is grounded in at least one
actual rescue_smoke rejection.

Format per pattern:
  ID         -- short snake_case key, used in code and logs
  signature  -- regex or substring that identifies the failure
  occurrences-- count in the seed window
  cause      -- root cause as best understood
  rule       -- prescriptive instruction the builder/directive generator must follow
  detect_pre -- optional static check the directive generator can run BEFORE
                emitting the directive
  detect_post-- optional static check rescue_smoke can run AFTER generation
                but BEFORE deploy

## Idempotence
This file is rewritten in full by `meta_loop_curator.py` after each curation
cycle. Re-running a curation cycle on identical inputs produces an identical
file. Manual edits should be made via PR-style suggestion in
`/home/workspace/shared/work/antipattern_proposals/` and merged by the curator.

---

## AP-001  write_service_called_as_function

  signature   : "wiring: write_service() called as function"
  occurrences : 5 / 11 (45%)
  cause       : Builder produces code that imports/invokes `write_service`
                like a Python function. write_service is a separate HTTP
                service at http://127.0.0.1:8772/write, NOT a callable.
                This pattern persisted across 6 days and 5 distinct tasks --
                strong evidence the directive_generator is not steering away
                from it.
  rule        : Generated code that needs to write to DuckDB MUST use one of:
                (a) urllib.request.urlopen against http://127.0.0.1:8772/write,
                (b) the helper `ws_write(table, row)` if already in scope,
                (c) httpx.AsyncClient().post against the same URL.
                Generated code MUST NOT contain `from .* import write_service`,
                `import write_service`, or `write_service(` as a function call.
  detect_pre  : In the directive prompt, prepend an explicit
                'NEVER call write_service() -- it is an HTTP service' clause.
  detect_post : grep -E 'write_service\\s*\\(' generated_file.py  -> reject if match.

## AP-002  unterminated_string_literal_line_1

  signature   : "SyntaxError line 1: unterminated string literal"
  occurrences : 2 / 11 (18%)
  cause       : MiniMax (or other model) responses sometimes begin with a
                stray triple-quote or backtick fence that survives stripping.
                Builder writes the raw text to disk; rescue_smoke import
                fails on parse.
  rule        : All generation pipelines MUST run
                `compile(source, filename, 'exec')` BEFORE writing to disk.
                If compile raises, do not write -- ask the model again with
                'previous response had a SyntaxError on line N: ...' appended
                to the prompt.
  detect_pre  : Add 'Output ONLY runnable Python. No code fences, no markdown,
                no preamble.' to all builder prompts.
  detect_post : Mandatory `compile()` gate in rescue_smoke before file write.

## AP-003  duckdb_executescript

  signature   : "wiring: executescript() not in DuckDB"
  occurrences : 1 / 11 (9%)
  cause       : Builder generated SQLite-style code (`conn.executescript(...)`)
                against a DuckDB connection. DuckDB connection objects do not
                expose executescript().
  rule        : When the target DB is DuckDB, multi-statement SQL must be
                executed by splitting on `;` and running each via `conn.execute()`,
                or by using `duckdb.connect().sql()` with a single string.
  detect_pre  : Directive prompts that mention DuckDB MUST include the
                'DuckDB has no executescript()' note.
  detect_post : grep `executescript` in generated file when target is DuckDB
                connection -> reject.

## AP-004  syntax_error_other_lines

  signature   : "SyntaxError line [^1]" (lines 3, 7, 214 observed)
  occurrences : 3 / 11 (27%)
  cause       : Generic syntactic invalidity not captured by AP-002. Likely
                truncation at max_tokens, mismatched brackets, or model
                drift mid-response.
  rule        : Same as AP-002 -- mandatory compile() gate. On compile failure,
                the rescue_smoke output should include the offending line
                window (line N ± 3) so the next attempt can self-correct.
  detect_pre  : Increase max_tokens for tasks > 200 lines. Use streaming
                aware truncation detection (response ends mid-statement).
  detect_post : Mandatory `compile()` gate.

---

## Curation history

  2026-04-26  v0.1  Seeded by Claude (CEO) from 11 build_failed events between
                    2026-04-20 and 2026-04-26. 4 antipatterns extracted.
                    AP-001 is the dominant pattern; AP-002 and AP-004 share
                    the compile() gate as their countermeasure.