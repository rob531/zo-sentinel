# Meta-Loop Spec: builder-builder  (v0.2, 2026-04-26)

v0.2 corrects v0.1 by aligning to the canonical patterns already running
in the system, not inventing parallel ones.

## Canonical patterns this spec must adopt

ZoComputer-side daemons follow the **wrapper-supervised** pattern:

  supervisord  ->  bash daemon_wrapper.sh <n> <script>  ->  python script

The wrapper (zo_mesh/daemon_wrapper.sh) provides graduated backoff (2/4/8/...30s)
and a crash-rate ceiling (5 in 60s -> 5min cool-down). supervisord keeps the
wrapper alive; the wrapper keeps the daemon alive. rc=0 from the daemon = clean
shutdown, no respawn. Wrapper log is /home/workspace/logs/wrapper_<n>.log
separate from the daemon's own stdout/stderr.

Tower-side dissolvable scripts follow the **Invoke-Probe.ps1** pattern:

  param(...) + [switch]$DryRun
  $ErrorActionPreference = "Stop"
  $startUtc = (Get-Date).ToUniversalTime()
  ...do work, exit fast...
  atomic write: $tmp = $path + ".tmp"; [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom); Move-Item -LiteralPath $tmp -Destination $path -Force

Key rules:
  - PowerShell 5.1 'Set-Content -Encoding UTF8' writes a BOM. Python's json.loads
    rejects BOM. ALWAYS use [System.IO.File]::WriteAllText with
    (New-Object System.Text.UTF8Encoding $false). Consumers read 'utf-8-sig'.
  - Output filenames: <type>_<utc_yyyymmdd_hhmmss>_<id>.json
  - Output dir under C:\Users\robin\ZoComputer\shared\outputs\<type>\
  - State / local logs under C:\Users\robin\ZoComputer\state\ (NOT shared\)
    so they don't replicate to ZoComputer and don't pollute the sync folder.

Tower-side worker dispatchers follow the **zo_warm_worker.ps1** pattern:
  - Triggered by Windows Scheduled Task (every 60s + at logon)
  - Lockfile-guarded: refuses to start if previous run still holds the lock
  - Polls a single inbox dir (e.g. shared\work\<type>\)
  - Dispatches each spec to a single-purpose Invoke-* script
  - Moves processed specs to processed/ or failed/ subdirs
  - Dissolves on completion (no resident state)

## What it is

A second-order builder. Watches failure artefacts produced by the first-order
builder (zo_sentinel_builder) and the directive_generator. Produces patches
to the artefacts that change future builder behaviour: KNOWLEDGE_BASE.md,
SENTINEL_DIRECTIVE_SCHEMA.md, BUILDER_ANTIPATTERNS.md, and (gated) the
directive_generator prompt itself.

It is a curator, not a coder. It does not write the next directive. It writes
the rules that constrain how the next directive is written.

## Why tower for the curator

- Free CPU. ZoComputer minutes cost; tower minutes don't.
- Async. Curation runs every N hours, file-bridge latency is irrelevant.
- Decoupling. If the curator is wrong, it can't crash the production builder.
- Memory-stable. Curation reads 100s of failure events; tower 16GB is ample.
- Reuses the existing dissolvable + lockfile pattern -- zero new mechanism.

## Inputs (synced via Bridge 5: Syncthing)

  ZoComputer:  /home/workspace/shared/inbox/meta_loop/
  Tower:       C:\Users\robin\ZoComputer\shared\inbox\meta_loop\

    ├ build_failed_<utc>_<hash>.json     one per build_failed event
    ├ build_complete_<utc>_<hash>.json   one per success (positive examples)
    ├ escalation_call_<utc>_<hash>.json  inference latency / failure stats
    ├ BUILDER_ANTIPATTERNS.md            current antipattern set (snapshot)
    ├ SENTINEL_DIRECTIVE_SCHEMA.md       current schema (snapshot)
    └ KNOWLEDGE_BASE.md                  current builder KB (snapshot)

## Outputs (synced back to ZoComputer)

  Tower:       C:\Users\robin\ZoComputer\shared\work\meta_loop_proposals\
  ZoComputer:  /home/workspace/shared/work/meta_loop_proposals/

    ├ antipatterns_proposed_<utc>.md     proposed BUILDER_ANTIPATTERNS.md updates
    ├ schema_diff_proposed_<utc>.md      proposed schema additions
    ├ prompt_diff_proposed_<utc>.md      proposed directive_generator prompt changes
    └ curation_report_<utc>.md           rationale + evidence per proposal

## Components and exact paths

### ZoComputer-side

  /home/workspace/zo_sentinel/meta_loop_exporter.py
      Polls mesh_events on 30-min cadence, dumps slices since last_exported_id
      as <event_type>_<utc>_<hash>.json into shared/inbox/meta_loop/.
      Tracks last_exported_id in /home/workspace/zo_sentinel/state/meta_loop_exporter.state
      Idempotent: re-running with no new events writes zero files.

  /home/workspace/zo_sentinel/meta_loop_merger.py
      Polls shared/work/meta_loop_proposals/ on 5-min cadence.
      Auto-merges low-risk class (BUILDER_ANTIPATTERNS additions only, requires
        ≥3 fresh evidence events cited).
      All other proposals require an APPROVED_<file> sentinel from human.
      Tracks merged proposals by sha256(file) in state/meta_loop_merged.json.
      Re-running on identical state is a no-op.

  Both run under daemon_wrapper.sh:
      [program:meta_loop_exporter]
      command=bash /home/workspace/zo_mesh/daemon_wrapper.sh meta_loop_exporter \
              /home/workspace/zo_sentinel/meta_loop_exporter.py
      ...
      [program:meta_loop_merger]
      command=bash /home/workspace/zo_mesh/daemon_wrapper.sh meta_loop_merger \
              /home/workspace/zo_sentinel/meta_loop_merger.py
      ...

### Tower-side

  C:\Users\robin\ZoComputer\shared\code\tower\meta_loop\Invoke-MetaLoopCurator.ps1
      Dissolvable. param(-InboxPath, -OutputPath, -DryRun).
      $ErrorActionPreference = 'Stop'.
      Reads inbox JSON files, runs clustering against current antipatterns,
      writes proposal markdown files to OutputPath.
      Atomic .tmp + Move-Item, UTF-8 NO BOM, filename pattern:
        <kind>_proposed_<yyyymmdd_hhmmss>.md
      No daemon. Exits when work queue is empty.

  C:\Users\robin\ZoComputer\shared\code\tower\meta_loop\zo_meta_loop_worker.ps1
      Lockfile at C:\Users\robin\ZoComputer\state\meta_loop_worker.lock
      (NOT shared\, doesn't replicate).
      Polls shared\inbox\meta_loop\ for new files since last watermark.
      Dispatches batch to Invoke-MetaLoopCurator.ps1.
      Writes local rotating log to state\meta_loop_worker.log.
      Dissolves on completion.

  Scheduled Task: ZoMetaLoopWorker
      Trigger: every 60 minutes + at logon (the curator can run less often
               than the probe worker; 60min is enough)
      Action:  PowerShell -ExecutionPolicy Bypass -File
               C:\Users\robin\ZoComputer\shared\code\tower\meta_loop\zo_meta_loop_worker.ps1
      Conditions: Run only if network available; do not start if on battery
                  (laptop carry-over, harmless on the desktop tower)

## Loop sequence

  T+0       meta_loop_exporter dumps last 30 min of mesh_events to inbox/meta_loop/
  T+0..60s  Syncthing replicates to tower
  T+60min   ZoMetaLoopWorker scheduled task fires
            ├ lockfile check: skip if previous still running
            ├ poll inbox\meta_loop\ for new files
            ├ dispatch to Invoke-MetaLoopCurator.ps1
            └ dissolve
  T+60..65m Curator runs:
            ├ read all inbox files since last watermark
            ├ read current BUILDER_ANTIPATTERNS.md / schema / KB snapshots
            ├ cluster failures by signature (regex match against AP-NNN)
            ├ per cluster:
            │   - existing AP + ≥3 new occurrences -> propose update
            │   - existing AP + 0 new in 14 days -> propose deprecation
            │   - unmatched cluster (≥2 occurrences, distinct tasks)
            │         -> propose new AP-NNN
            └ write proposed_*.md to shared\work\meta_loop_proposals\
  T+5m+     Syncthing replicates back to ZoComputer
  T+next    meta_loop_merger polls proposals dir
            ├ auto-merge low-risk (AP additions w/ ≥3 evidence)
            └ wait for APPROVED_<file> sentinel for everything else
  T+next+   directive_generator + zo_sentinel_builder pick up updated
            BUILDER_ANTIPATTERNS.md on next prompt build

## Idempotence guarantees

  exporter:  last_exported_id watermark; double-run with no new events = 0 writes
  curator:   reads inbox + state; outputs filenames hashed by content;
             identical inputs -> identical outputs (Syncthing dedups)
  merger:    sha256-tracked; never merges same file twice
  ALL three honour rc=0 = clean exit, allowing the wrapper to stop the program
  cleanly when the queue is empty.

## First measurable success criterion

AP-001 (write_service_called_as_function) is 5/11 = 45% of build failures
in the seed window. After:
  (a) BUILDER_ANTIPATTERNS.md is in scope of the directive_generator prompt
  (b) rescue_smoke gains a `grep -E 'write_service\\s*\\(' generated_file.py` reject

AP-001 occurrences should drop to zero in the next 7 days. If they do, the
antipattern memory + rescue_smoke gate worked even before the curator runs
once. If they don't, the gate isn't being applied and the loop is broken.

## Build order (each step reversible)

  1. rescue_smoke compile() gate                       (zero-dependency safety net)
  2. rescue_smoke detect_post grep for AP-001          (uses BUILDER_ANTIPATTERNS.md)
  3. meta_loop_exporter under daemon_wrapper            (write-only, can't break anything)
  4. Tower zo_meta_loop_worker.ps1 + scheduled task     (lockfile-guarded, dissolvable)
  5. Tower Invoke-MetaLoopCurator.ps1 in --DryRun mode  (writes proposals, no merge)
  6. meta_loop_merger with auto-merge OFF               (proposals visible, manual approval)
  7. enable auto-merge for AP additions only            (after 1 week of clean dry-run)
  8. enable auto-merge for AP deprecations              (after 4 weeks)
  Prompt-diff merges remain ALWAYS human-gated.

## Out of scope (explicitly)

- Generating the next directive itself. That is zo_sentinel_builder's job.
- Touching production agents directly. The meta-loop only writes to the
  artefacts the production agents read.
- Calling expensive LLMs. Curation uses tower phi3:mini for clustering and
  cause-hypothesis. Anthropic-level inference is unnecessary for this workload.
- Inventing new transports. Uses Bridge 5 (Syncthing) for tower<->ZoComputer.
- Inventing new supervision. Uses daemon_wrapper.sh on ZoComputer side and
  the lockfile-dissolvable pattern on tower side.