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

## When the student model is ready

1. Wire a real dispatcher (shells out to the sft repo's `dispatch_vast_v3.sh` /
   `install_sky_dispatcher.sh`) and set `dispatch.dry_run=False` on the job.
2. Open an activation latch (`SFT_BATCH_ENABLED=1` or drop `.batch_enabled`).
3. `BatchRunner(queue, dispatcher=RunpodDispatcher()).drain()` claims queued
   jobs and dispatches them.

## CLI

```bash
python -m zo_sentinel.sft status              # queue snapshot + dormancy state
python -m zo_sentinel.sft validate job.json   # validate only
python -m zo_sentinel.sft submit  job.json    # validate + enqueue
python -m zo_sentinel.sft list    queued      # list (optionally by status)
```

Tested hermetically in `tests/test_sft_ingest.py` and import-gated by the CI
smoke ladder (`tests/ci/hermetic_manifest.py`).
