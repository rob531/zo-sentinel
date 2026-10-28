# `zo_sentinel.sft` — SFT training-job ingestion

The intake/staging layer between the zo-sentinel trust pipeline (which produces
teacher corrections / labelled corpora) and the **zomesh-sentinel-sft** training
pipeline (SkyPilot/RunPod cloud-GPU batches that fine-tune the student LoRA
adapter).

Built to **receive jobs now and stay dormant** until the student model is ready
for batch running. Ingestion, validation, and queueing are live; the batch
runner refuses to claim or dispatch until explicitly activated, and the default
dispatcher only *records* intent — it never launches a GPU.

## Pieces

| Module | Role |
|---|---|
| `schema.py` | `JobSpec` + `JobStatus` + pure validator. Dataset formats mirror the SFT repo: `messages` (chat SFT) and `preference` (DPO). |
| `ingest.py` | `IngestQueue` — file-per-job, status-as-directory, atomic writes. `submit()` validates + stamps the real row-count/sha256, then `QUEUED` or `REJECTED`. |
| `batch_runner.py` | `BatchRunner` — claims `QUEUED` jobs **only when activated**; `NoopDispatcher` records a dry-run and never launches. |
| `__main__.py` | CLI: `status` / `validate` / `submit` / `list`. |

## Dormancy — two latches, both must open to dispatch for real

1. **enabled** — `BatchRunner(enabled=True)`, or `SFT_BATCH_ENABLED=1`, or a
   `.batch_enabled` sentinel file in the queue dir. Default: dormant.
2. **dispatcher** — defaults to `NoopDispatcher` (dry-run only). A real
   RunPod/SkyPilot dispatcher is a separate, explicit wiring step.

So importing or exercising this package can never start a cloud job by accident.

## When the student model is ready — ONE flag

`ZO_SFT_LABEL_LOOP_ARMED=1` is the single documented switch
(design of record: `docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md` §3.5).
`zo_sentinel.sft.dispatcher.build_runner(queue)` reads it and returns either
the stock dormant runner (flag closed — NoopDispatcher, latches shut) or an
armed runner with the real `ShellDispatcher`, which shells out to
`$ZO_SFT_DISPATCH_CMD` (the sft repo's `dispatch_vast_v3.sh` /
`install_sky_dispatcher.sh`) and still honours per-job `dispatch.dry_run`.
The two legacy latches below remain underneath as defense in depth —
`ShellDispatcher` independently refuses when the flag is closed — but no
operator coordinates them by hand anymore:

1. ~~Wire a real dispatcher~~ → `build_runner` selects it from the flag.
2. ~~Open an activation latch~~ → the flag opens it.
3. `build_runner(queue).drain()` claims queued jobs and dispatches them.

The corpus/retrain producer that FEEDS this queue is `zo_sentinel.label_loop`
(1k-registry-row trigger, teacher pass, quarantine for empty teacher returns,
discrimination gate). `python -m zo_sentinel.label_loop status` shows both
sides' state in one JSON.

## CLI

```bash
python -m zo_sentinel.sft status              # queue snapshot + dormancy state
python -m zo_sentinel.sft validate job.json   # validate only
python -m zo_sentinel.sft submit  job.json    # validate + enqueue
python -m zo_sentinel.sft list    queued      # list (optionally by status)
```

Tested hermetically in `tests/test_sft_ingest.py` and import-gated by the CI
smoke ladder (`tests/ci/hermetic_manifest.py`).
