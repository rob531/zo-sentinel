# Builder Antipatterns -- v0.3 (seeded 2026-04-26, extended 2026-08-27, 2026-11-27)

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

## AP-005  module_name_used_as_table_name

  signature   : `FROM <x>` / `JOIN <x>` / `UPDATE <x>` where `<x>.py` exists in
                the repo root but `<x>` exists on NO plane.
  occurrences : 2 / 18 phantom table names in #4080 (11%)
  cause       : The emitter had the module in context, read the NAME as a data
                source, and wrote SQL against it. Both instances are files that
                genuinely relate to the data being queried, which is exactly
                why it reads as plausible:

                  known_threats.py      a module of static CONSTANTS
                                        (KNOWN_MALICIOUS_PACKAGES,
                                        HIGH_RISK_PATTERNS) imported by
                                        signal_analyser.py. `SELECT ... FROM
                                        known_threats` in sentinel_cli.py.
                  approval_workflow.py  the approval SERVICE on port 8780. It
                                        WRITES mcp_submissions and
                                        mcp_decisions. `INNER JOIN
                                        approval_workflow` in
                                        snow_connector_approval_integration.py.

  why it hid  : A module name is the most plausible possible wrong table name.
                It is a real identifier, in the right domain, spelled correctly,
                and it survives every static check -- the SQL is well-formed and
                the name resolves to something real, just not to a table. Only
                resolving it against a catalog can tell.
  rule        : A table name must resolve on the bus catalog, in app/models.py
                as a __tablename__, or in migrations/versions/. If the only
                thing in the repo bearing that name is a .py file, it is a
                MODULE, and a module is not a data source. Import it or query
                the table it writes -- never both at once.
  detect_pre  : When a directive's grounding context includes module names
                alongside table names, the emitter must be told which list is
                which. Never present them in one undifferentiated blob.
  detect_post : tools/referent_verify.py TABLES check, ARMED. A table naming a
                repo-root .py module and nothing else is this antipattern.

## AP-006  phantom_table_in_a_sql_string

  signature   : A table name inside a SQL STRING LITERAL, in a module that
                addresses the write-service bus on :8772, that exists on no plane.
  occurrences : services/staged/circuit_breaker_status_api/contract.py,
                2026-08-25 -- AFTER the 2026-08-11 grounding fix
  cause       : The AST schema linter (schema_kl.lint_source) checks PYTHON
                schema surface: constructor kwargs, model attribute access,
                inline declarative_base. A table named in a SQL string has NONE
                of those. It presents no Python surface at all, so every AST
                check saw nothing and passed it.
  rule        : Bus-bound SQL is checked against the union catalog like any
                other referent. Name a real table, or ship a migration that
                creates it.
  detect_pre  : Grounding context must carry the real table list for any
                directive whose output posts to :8772.
  detect_post : schema_kl.lint_sql_referents(), BLOCKING at the emission gate
                (goose_runner._schema_prm_gate) since #4068, and the armed
                referent-verify TABLES check as the merge-time backstop.
                Emission-gate coverage is the load-bearing one -- the CI
                schema-prm gate inspects root-level modules only, so a
                services/ emission never reaches it.

## AP-007  consumer_without_a_producer

  signature   : A module SELECTs from a queue/results table and UPDATEs it to
                acknowledge, and NOTHING in the tree ever INSERTs into it.
  occurrences : 3 -- mcp_enrichment_work_queue (enrichments_writer.py),
                mcp_pi_results (approval_evidence_bundler.py), and
                approval_workflow's snow_ticket_id
                (snow_connector_approval_integration.py)
  cause       : The emitter built one half of a handshake. A consumer is a
                complete, coherent, testable-looking module on its own, so
                nothing about it looks unfinished -- and its self-test can pass
                against a MOCK endpoint that answers a question the real bus
                cannot. enrichments_writer's self-test asserted one queue item
                against a mock /query for months.
  why it hid  : This one does not fail loudly. The query raises, the handler
                catches it, the function returns [], and the daemon loops
                forever doing nothing. A pipeline stage that processes zero
                items looks exactly like a pipeline stage with no work.
  rule        : Do not emit a consumer for a table no producer writes. If the
                directive is the consumer half, it must either name the
                existing producer or declare the table itself (a migration, or
                an ensure_tables() the module owns -- the way
                snow_connector_approval_integration owns snow_approval_status).
  detect_pre  : A directive that says "poll/consume/process a queue" must name
                the producer in its grounding context.
  detect_post : referent-verify TABLES catches it when the table is on no
                plane. It does NOT catch a real-but-never-written table -- for
                that, a zero-row producer check is still owed.

## AP-008  fly_route_addresses_the_factory_bus

  signature   : A module the Dockerfile copies into the Fly image calls
                http://127.0.0.1:8772 (/query, /execute, /write) in a request path.
  occurrences : 10 files, 18 sites, census 2026-11-27 (GR-29): 5 mounted root
                routers, 4 services/active package __init__ files, 1 dead constant.
                Earlier: /registry/freshness-report, /scoring/waves/summary (#7016).
  cause       : Plane confusion. :8772 is the factory write_service on the
                ZoComputer host; the product app on Fly reads Postgres through
                app.db.get_session. The emitter learned "data lives behind :8772"
                from the factory side and applied it to Fly-bound routers.
  why it hid  : On Fly the call fails with ECONNREFUSED. Half the sites wrapped it
                in `except: pass` and returned [] -- an empty answer that looks
                like no data. AP-006 checks the TABLE names inside :8772 SQL; it
                never asked whether the module may address :8772 at all.
  rule        : Fly-bound code reads and writes through get_session (SQLAlchemy,
                parameterized). If the data has no Postgres plane, the route does
                not exist yet -- do not reach across to the factory bus.
  detect_pre  : Grounding for any directive whose output_path the Dockerfile
                ships: "read via app.db.get_session; never 127.0.0.1:8772".
  detect_post : tools/fly_bus_plane_check.py (AST over every Dockerfile COPY
                source outside tools/; docstrings, comments and __main__
                self-tests excluded), BLOCKING via tests/test_gr29_fly_bus_plane.py
                in the required pytest context.

## AP-009  single_shot_outbound_http

  signature   : A bare requests.get/post (or httpx) call to an external host that
                is not inside a retry loop and does not go through http_retry.
  occurrences : 5 files in this repo, census 2026-11-27 (GR-4):
                ecosystems_metadata_fetcher, otx_ingestor, mcp_registry_ingestor,
                mcp_reference_servers_ingestor, signal_bridge. 6 more on the
                ZoComputer host (GR-31: zo_mesh/* and zo_sentinel_builder).
  cause       : The emitter writes the happy path. One request, one response,
                parse, done -- which is what every example it learned from shows.
  why it hid  : A transient 5xx or reset kills one cycle, the daemon logs it and
                tries again next interval. Nothing is ever "down"; an ingestor
                just quietly misses a fraction of its sources every day.
  rule        : Factory (host) code: every outbound call goes through
                http_retry.request_with_retry(method, url, idempotent=...).
                Fly-bound code (app/, services/, routers): http_retry.py is NOT
                in the Fly image, so a bounded retry loop lives in the module.
                Reads are idempotent=True. Writes are idempotent=False and are
                retried only on ConnectionError -- never re-send a write that may
                already have applied. A deliberate single shot is listed with its
                reason, not left implicit.
  detect_pre  : goose_runner_lib/context.py _steer_context -- the AP-009 line is
                attached when the directive names outbound HTTP (a URL, requests/
                httpx, webhook, crawl, or fetch/poll/ingest next to a feed, API or
                named source -- not a DB "fetch rows"); the Fly variant is chosen
                when output_path is Fly-bound.
  detect_post : tests/test_gr4_retry_sweep.py (required pytest): AST census of
                bare calls in the swept files, INTENTIONAL_SINGLE_SHOT allowlist,
                and the helper's contract.

## AP-010  signal_reads_its_own_output

  signature   : A compute_* signal function reads trust_score, verdict or
                last_assessed -- columns process_server writes back from the
                composite.
  occurrences : 1 -- `reputation` (weight 0.20) in signal_analyser.py returned
                the stored trust_score (GR-28, removed in #16244).
  cause       : "Reputation" sounds like an input. The emitter reached for the
                column that looked most like reputation, and that column is the
                scorer's own previous answer.
  why it hid  : The input-hash gate excludes trust_score, so the loop was
                invisible to it, and the output looked stable -- because it was
                partly scoring itself. A fifth of the composite carried no
                information and nothing failed.
  rule        : A signal scores evidence (registry fields, fingerprints, threat
                rows, scan results), never a column the scorer writes. If a
                signal needs history, read mcp_assessment_history and say so.
  detect_pre  : goose_runner_lib/context.py _steer_context -- the AP-010 line is
                attached when the directive mentions signal, score, scorer,
                scoring, composite or compute_.
  detect_post : tests/test_gr28_no_trust_echo.py (required pytest): AST check that
                no compute_* names a written-back column (negative control on the
                old reputation source), and the composite is invariant to the
                prior trust_score.

---

## Curation history

  2026-04-26  v0.1  Seeded by Claude (CEO) from 11 build_failed events between
                    2026-04-20 and 2026-04-26. 4 antipatterns extracted.
                    AP-001 is the dominant pattern; AP-002 and AP-004 share
                    the compile() gate as their countermeasure.

  2026-08-27  v0.2  AP-005..AP-007 extracted from the #4080 phantom-table
                    remediation -- the 18 names referent-verify could not
                    resolve, traced one at a time to their intended referent.

                    These three differ in kind from AP-001..AP-004. Those are
                    SYNTAX failures: the build breaks and you find out at once.
                    These are REFERENCE failures. The code compiles, passes
                    ruff, passes the reachability ratchet, passes the schema
                    PRM, and names something that does not exist -- and the
                    exception it eventually raises is caught by the module's
                    own error handler and turned into an empty list. That is
                    why they accumulated for four months while the syntax
                    antipatterns above were closed in a week.

                    Every one is now detected by an ARMED check rather than by
                    prose, which is the point: AP-001's rule lived in this file
                    and in directive prompts and leaked anyway, until it lived
                    in code.

  2026-11-27  v0.3  AP-009 (GR-4) and AP-010 (GR-28) added after their gates
                    were armed, so the emitter is taught what the gate already
                    refuses. Both carry a detect_pre steer in the engine prompt
                    (_steer_context) as well as the blocking detect_post.
