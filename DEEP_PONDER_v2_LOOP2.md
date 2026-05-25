# ZO Sentinel Builder — Recursive Evaluation v2.0
## Loop 2 of N: First re-evaluation with expanded data
## Written: 2026-04-16, after reading roadmap + BUILD_MANIFEST + full log tail
## Incremental data vs v1.0: roadmap (phases 2-10), BUILD_MANIFEST (hundreds of builds), 200-line log tail

> The original memo (v1.0) was thoughtful but diagnosed against incomplete data.
> This version corrects several material errors and adds new findings.
> Improvements from v1.0 are marked [NEW] or [CORRECTED].

---

## I. What the Original Memo Got Wrong

### [CORRECTED] The Three April-11 Smoke Failures Are Already Resolved

The v1.0 memo spent significant attention on signal_analyser.py (import whois),
rug_pull_monitor.py (from duckdb import DuckDB), and registry_api.py (missing Any).
All three files were successfully rebuilt by the builder's auto-restore mechanism.
The current files are clean v1.1 versions with no trace of those errors.

This was a ghost diagnosis. The memo was analysing stale entries in mesh_memory
rather than checking the actual files on disk.

### [CORRECTED] The Builder Was NOT Primarily Stuck

The v1.0 memo characterised the system as "stuck on auto_dependency_resolver."
The BUILD_MANIFEST tells a different story: the builder was processing dozens
of builds per day (config_validator, graphql_schema, approval_workflow.jsx,
phase6b supervisord config, mcp_detail_view.html, and more) right up until 17:30
UTC April 15. The builder was productive. It was NOT primarily stuck.

What happened at 17:30 was: the builder entered the auto_dependency_resolver loop
AND processed 5 directives in one cycle (17:29 shows "Found 5 directives"), then
started on build_threat_feed_api and the log cuts off mid-build. The process died.

### [CORRECTED] The Director Starvation Is Not the Core Problem

The 6h director vs 5min builder frequency mismatch is a real structural issue,
but it is NOT why the queue is empty. The queue empties because:
  (a) The builder has chronic FAILERS that consume cycles without advancing the build
  (b) The builder process dies and needs manual restart

Fix the failers + fix the restart mechanism, and the director starvation
becomes less urgent.

---

## II. New Findings From Loop 2 Data

### [NEW] The Builder Process Is Dead, Not Sleeping

The log was last written 8.5 hours ago at 17:30 UTC. The 5-minute poll cycle
means that if the builder were alive and finding no directives, it would be
logging "No pending directives" every 5 minutes. The complete silence means
the process is dead, not sleeping.

A dead process at 17:30 is not explained by the scoping bug alone (which causes
a loop, not a death). Something killed the builder: OOM, supervisord restart,
or the ZoComputer scheduler intervening. The `build_threat_feed_api` build that
was starting when the log cut off may have caused an uncaught exception.

**Implication**: The scoping bug fix is necessary but not sufficient. The builder
also needs a supervisord health guard that restarts it on death. Separately,
the process should not be killable by a single directive failure.

### [NEW] Three Chronic Failer Modules Are Blocking Progress

The BUILD_MANIFEST reveals a pattern invisible from the smoke_fail entries:

  config_validator.py (Phase 10): 8+ consecutive failures across 2 days
    Pattern: "wiring: write_service() called as function" on rescue smoke
    Root cause: MiniMax generates `write_service(...)` as a function call;
    wiring check correctly rejects it; rescue generates the same pattern.
    This is a MiniMax output pattern problem, not a transient failure.

  graphql_schema.py (Phase 15): 8+ consecutive failures across 2 days
    Pattern: "Traceback line 7, File <frozen importlib...>" on rescue smoke
    Root cause: line 7 is typically the first import; graphql-core or
    strawberry-graphql is not installed in the ZoComputer Python env;
    pre-flight dependency resolver is failing to install it (probably
    because graphql packages require compilation or are network-blocked).

  ui/approval_workflow.jsx (Phase 4b): 8+ consecutive failures across 2 days
    Pattern: "too short (19 bytes)" = builder's [generation failed] string
    Root cause: MiniMax is returning empty on JSX generation. Either the
    prompt context (49KB by last cycle) is consuming most of MiniMax's
    context window, leaving no room for JSX output, or MiniMax's content
    filter is blocking JSX generation from a Python-centric prompt.

**These three modules are phantom directives**: they fail, get written to
.done.json as smoke_fail, auto_restore re-queues them, they fail again.
They will never succeed without targeted intervention.

### [NEW] Prompt Bloat Is a Growing Structural Problem

The rich_prompt size in the last log cycle:
  16:57 UTC: 48,357 chars
  17:02 UTC: 48,538 chars  (+181)
  17:08 UTC: 48,650 chars  (+112)
  17:13 UTC: 48,831 chars  (+181)
  17:18 UTC: 49,012 chars  (+181)
  17:24 UTC: 49,134 chars  (+122)
  17:29 UTC: 49,315 chars  (+181)

The prompt grows ~180 chars per cycle as the build state accumulates interface
summaries. At 49KB the prompt is consuming roughly 12-15K tokens of MiniMax's
context window. MiniMax's effective output window is shrinking each cycle.

This explains the varying output sizes for auto_dependency_resolver:
  2786b, 3310b, 3501b, 3792b, 4066b, 4203b, 4413b, 8623b (outlier)

The model's output quality is becoming dependent on which part of the 49KB
prompt it decides to attend to. Eventually, consistent generation fails.
"Too short (19 bytes)" is the end state of severe prompt bloat.

**The prompt bloat problem will eventually kill ALL high-complexity builds.**
This needs architectural attention: the build state context injected into
the rich prompt needs selective compression based on directive relevance.

### [NEW] The Nth Directive Mechanism IS Working

The SENTINEL_ROADMAP.md ends at Phase 10. The builder is currently processing
Phase 20 (build_threat_feed_api), Phase 26 (arcade_toolbench_ingestor), and
Phase 27 (auto_dependency_resolver). These phases don't exist in the roadmap.
They exist because the `inject_next_directive` chain mechanism is working:
some earlier directive contained a `next_directive` block that spawned these.

This is actually the self-propagating build graph working as designed.
The builder has grown the project organically beyond the initial roadmap.
This is good news. The Nth directive concept is validated by the system's
own behaviour.

The implication: the directive cannon (pre-loading all directives) is LESS
urgent than I thought in v1.0, because the chain mechanism is already doing
this. What's needed is to ensure the chain mechanism has good signal about
what to generate next, rather than pre-loading a static list.

---

## III. Revised Assessment: The Four Questions

### Can Builder Finish ZO Sentinel?

Revised answer: YES, but it needs four structural repairs before it can:

  1. Fix the _builds_this_session scoping bug (patch written and ready)
  2. Solve the three chronic failers:
     - config_validator: write a write_raw directive with correct implementation
     - graphql_schema: either remove from queue (not in official roadmap) or
       fix the import to use a pure-Python graphql library with no C deps
     - approval_workflow.jsx: reduce context in the JSX prompt OR write raw
  3. Address prompt bloat: implement context windowing in build_rich_prompt()
  4. Ensure supervisord restarts the builder on process death

With these four fixes: Phases 2-10 are achievable within the existing architecture.
Phases 11+ via the chain mechanism are already happening.

The quality ceiling remains: modules built with 49KB prompts will be hollow.
Quality pass directives (rebuild with Sonnet once core is done) remain valid.

### Can Builder Be Repurposed for Any Lightweight UI/UX App?

The v1.0 assessment was correct but incomplete. New insight from the manifest:

The builder has already TRIED to build JSX (approval_workflow.jsx) and HTML
(mcp_detail_view.html) and has FAILED CONSISTENTLY. This is not just a
theoretical limitation -- it's empirically demonstrated.

The failure modes are instructive:
  - JSX: MiniMax returns "[generation failed]" because the Python-centric 49KB
    prompt leaves no room for JSX generation
  - HTML: MiniMax generates syntactically invalid HTML with Python constructs
    bleeding through

To repurpose the builder for UI/UX apps, the following are required beyond
the v1.0 analysis:
  1. Separate prompt contexts (a UI-specific context of <10KB, not the 49KB
     Python-focused sentinel context)
  2. A lower-context "clean room" build mode where the prompt is reset to only
     app-specific context, with no bleed from the Python build state
  3. A JSX/HTML smoke test that uses eslint or html-validator instead of ast.parse

The clean room build mode is the critical missing piece.

### Can the Watcher Become Tetris?

New data from the manifest confirms this is feasible and the data structure is clear.
The BUILD_MANIFEST.md is essentially a ledger of every build event with phase,
task, file, and status. The Tetris board's state is derivable from this file.

Revised implementation detail: parsing BUILD_MANIFEST.md is sufficient for
a full watcher implementation. No need to tail the builder log in real-time
(though that gives animation). The manifest gives complete historical state.

One new observation: the board would show not just green/red but CHRONICALLY
FAILING modules as flashing red (8+ failures on the same directive). This
visual signal would make phantom directives immediately obvious.

I can build the Tetris watcher as a React artifact right now using the
manifest data structure. It would be static (snapshot) but complete.

### Can the Mesh Grow Beyond ZoComputer?

No new insights from Loop 2 data -- the v1.0 three-circle framework holds.
The pipeline_bridge log shows the mesh is working well: T1 agents producing
outputs, bridge consuming them, T2/T3 processing. The mesh is healthy.

The one new observation: ZO Sentinel's registry_api.py at port 8781 is already
built and imports correctly. One `supervisorctl start registry_api` command and
it's serving externally. The outward-facing mesh node is closer than the memo
suggested.

---

## IV. The Immediate Action List (Refined from v1.0)

In priority order, smallest to largest:

1. IMMEDIATE: Run the builder patch
   python3 /home/workspace/zo_sentinel/fix_builder_v193.py
   Then restart the builder (zm go or supervisorctl restart zo_sentinel_builder)

2. IMMEDIATE: Quarantine the three chronic failers
   Write write_raw directives for config_validator (bypass MiniMax)
   Move graphql_schema and approval_workflow.jsx to a separate stalled/ folder
   to prevent them from clogging the main directive queue

3. SHORT TERM: Implement context windowing in build_rich_prompt()
   Cap injected build state context at 15KB (select N most recent + N most relevant)
   This will significantly improve MiniMax output quality for all future builds

4. SHORT TERM: Add supervisord auto-restart for zo_sentinel_builder
   The builder dying silently for 8+ hours is unacceptable for an autonomous system

5. MEDIUM TERM: Build the Tetris watcher as React artifact

6. MEDIUM TERM: The Directive Cannon (write all remaining roadmap directives at once)

---

## V. Loop 3 Preview: What Additional Data Would Improve This Analysis?

The following data sources have NOT yet been examined:
  - The current directive queue (what's in directives/*.json right now?)
  - The .build_state.json file (what interfaces have been extracted?)
  - The world agent's cybersecurity topic memory (ZO Sentinel context)
  - The mesh_memory entries for zo_sentinel.smoke_fail (why graphql keeps failing)
  - The actual content of build_threat_feed_api directive (what was building when builder died?)

Reading any of these would produce a Loop 3 update with materially new findings.
This document should be considered a working hypothesis, not a final assessment.

---
*Incremental improvements in this version vs v1.0:*
*- Corrected ghost diagnosis (three April-11 failures)*
*- Identified builder process death vs scoping loop*
*- Identified three chronic failers from manifest*
*- Identified prompt bloat as a growing structural problem*
*- Confirmed Nth directive chain is working*
*- Improved 'can builder finish' with 4 concrete structural repairs*
*- Confirmed UI failure modes from empirical evidence*
*- Identified registry_api as already deployable*