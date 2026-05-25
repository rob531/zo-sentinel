# signal_label file bridge — design notes

*Drafted 2026-04-30 evening, while Phase B SFT training is queued for tomorrow.
Files below are written but **not yet executed**; they should not run until
an SFT model exists to call.*

## What this is

The end-to-end path for using a tower-hosted SFT student model (Phase B output)
to label MCPs in `mcp_server_registry`, replacing the Anthropic-API-based
teacher labelers (`signal_labeler.py`, `signal_labeler_sonnet.py`) for
student inference.

Written entirely on top of the existing 8-bridge topology. No new bridges,
no network overlay, no extra scheduled tasks. Slots cleanly into bridge 7
(ZO → tower exec via Syncthing + ZoWarmWorker).

## Components

| File | Side | Role |
|---|---|---|
| `signal_labeler_filebridge.py` | ZoComputer | Picks unlabeled MCPs, drops batch specs into `shared/work/probes/`. Fire-and-forget. |
| `Invoke-SignalLabel.ps1` | Tower | Dispatchable handler. Reads spec, loops over MCPs, calls local Ollama, writes result file. |
| `Test-SignalLabel.ps1` | Tower | Sibling smoke test, dry-run + optional `-Live`. |
| `signal_label_consumer.py` | ZoComputer | Daemon. Watches `shared/outputs/signal_label/`, ingests results into `signal_training_corpus`. Supervisord-managed for reboot survival. |

## Flow

```
  signal_labeler_filebridge.py
        │  (1) SELECT from mcp_server_registry, NOT EXISTS join with corpus
        │  (2) build user_prompt per MCP (same shape as teachers saw)
        │  (3) write spec to:
        ▼
  /home/workspace/shared/work/probes/signal_label_<batch_id>.json
        │  (4) Syncthing replicates ~10-60s
        ▼
  C:\Users\robin\ZoComputer\shared\work\probes\signal_label_<batch_id>.json
        │  (5) ZoWarmWorker scheduled task ticks every 60s
        │  (6) dispatch by probe_type='signal_label' -> Invoke-SignalLabel.ps1
        ▼
  Invoke-SignalLabel.ps1
        │  (7) loop over items, POST to http://localhost:11434/api/generate
        │     model=<student tag>, system=SYSTEM_PROMPT, prompt=user_prompt
        │  (8) write result with BOM-less UTF-8 to:
        ▼
  C:\Users\robin\ZoComputer\shared\outputs\signal_label\<batch_id>_<ts>_result.json
        │  (9) Syncthing replicates ~10-60s
        ▼
  /home/workspace/shared/outputs/signal_label/<batch_id>_<ts>_result.json
        │  (10) signal_label_consumer.py polls every 5s
        │  (11) parse each item's response_text (same parser as labelers)
        │  (12) write 6 signal rows per ok MCP to signal_training_corpus
        │        with teacher_model=<student tag>
        ▼
  DuckDB: signal_training_corpus (UNIQUE on server_id+signal+teacher_model)
```

Round trip per batch: typically 2-4 minutes for a 50-MCP chunk. Larger
batches scale linearly on tower-side throughput.

## Why specs land in `shared/work/probes/` not a sibling directory

ZoWarmWorker's scheduled task watches `shared/work/probes/` and dispatches
by `probe_type`. Adding a sibling directory would need:
- a second scheduled task, OR
- a parallel watcher in the existing task with separate persistence

Neither is worth it. Discriminating by `probe_type` in the existing
dispatcher is the cleanest extension. The whitelist convention from
BRIDGES.md already accommodates new probe_types as additive entries.

## Why results land in `shared/outputs/signal_label/` not `shared/outputs/probes/`

`probe_consumer.py` owns `shared/outputs/probes/` and ingests into
`mesh_events` as `event_type='probe_result'` plus emits
`improvement_candidate` for WARN/ERROR severity. None of that semantics
applies to signal_label results, which fan out to 6 rows per MCP in a
different table.

Carving out a separate output directory means:
- two consumers, each with one job, no cross-coupling
- probe_consumer logic stays untouched; we ship without risk to existing flow
- per-consumer dedup, per-consumer heartbeat, separate failed/ tray

## Why we don't change `signal_labeler.py` or `signal_labeler_sonnet.py`

Those are the *teacher* labelers. They submit to Anthropic Batches and
are the canonical source of training data. Phase A is complete; teachers
should remain a stable artifact. The student dispatcher is a new file,
not a refactor of the teacher.

The *student* run uses `teacher_model='qwen2.5-3b-sentinel-v1'` (or
whatever tag we settle on) which is distinct from the existing
`claude-haiku-4-5-20251001` and `claude-sonnet-4-5` rows. The UNIQUE
constraint on `(server_id, signal_name, teacher_model)` accommodates all
three teachers + future students side-by-side without collision.

## Persistence per the runbook

`signal_label_consumer.py` is the only long-running daemon in this set.
It MUST be registered in `/etc/zo/supervisord-user.conf` to survive
Modal container reboots (failure mode #7 in temporal_checks.md).

Supervisord block to add (Robin owns this file edit):

```ini
[program:signal_label_consumer]
command=/usr/bin/python3 /home/workspace/zo_sentinel/signal_label_consumer.py
directory=/home/workspace
autostart=true
autorestart=true
stdout_logfile=/home/workspace/logs/signal_label_consumer.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
redirect_stderr=true
environment=PYTHONUNBUFFERED="1"
```

`signal_labeler_filebridge.py` is one-shot, so it doesn't need supervisord
— it's invoked manually for the 20K cold-start, then by the directive
generator once we wire it up.

## When this becomes live

The handler files are written but inert until:

1. Phase B SFT completes on RunPod (v1 + v2)
2. Eval picks the winning student
3. Merged adapter gets converted to gguf
4. gguf gets dropped on the tower (via Syncthing, 200MB+, watch the size)
5. `ollama create qwen2.5-3b-sentinel-v1 -f Modelfile` on the tower
6. The dispatcher edit (Option A in the README) is made
7. `signal_label_consumer` is registered in supervisord
8. Run the smoke first: `python3 signal_labeler_filebridge.py --smoke`
9. If smoke OK, run for real (`--max-mcps 200` first, then 1000, then full)

The 20K cold-start is naturally a handful of `signal_labeler_filebridge.py`
invocations spread over a day or two, depending on tower throughput. Same
pattern as the existing teacher labelers: idempotent, resumable,
fire-and-forget.

## What I will not do

- Will not propose a Tailscale / WireGuard / Cloudflare Tunnel bridge
- Will not add a synchronous HTTP path from `inference_router_service.py` to tower
- Will not modify `probe_consumer.py` or `signal_labeler.py`
- Will not touch `/etc/zo/supervisord-user.conf` directly (Robin owns)
- Will not promote the dispatcher to a daemon — it's one-shot by design

## Files written tonight

- `/home/workspace/zo_sentinel/signal_labeler_filebridge.py` (450 lines)
- `/home/workspace/zo_sentinel/signal_label_consumer.py` (348 lines)
- `/home/workspace/shared/code/tower/signal_label/Invoke-SignalLabel.ps1` (230 lines)
- `/home/workspace/shared/code/tower/signal_label/Test-SignalLabel.ps1` (106 lines)
- `/home/workspace/shared/code/tower/signal_label/README.md` (101 lines)
- `/home/workspace/zo_sentinel/SIGNAL_LABEL_FILEBRIDGE.md` (this file)

All inert until Phase B produces a model.