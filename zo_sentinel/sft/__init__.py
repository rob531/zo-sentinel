"""
zo_sentinel.sft -- SFT training-job ingestion for zo-sentinel.

This package is the intake/staging layer between the zo-sentinel trust pipeline
(which produces teacher corrections / labelled corpora) and the
zomesh-sentinel-sft training pipeline (SkyPilot/RunPod cloud-GPU batches that
fine-tune the student LoRA adapter).

It is built to RECEIVE jobs now and stay DORMANT until the student model is
ready for batch running -- ingestion + validation + queueing are live, but the
batch runner refuses to dispatch until explicitly activated, and dispatch is a
record-only stub (it never launches a GPU job from here).

    schema.py        JobSpec + JobStatus + validation contract
    ingest.py        file-backed IngestQueue: submit / list / claim / status
    batch_runner.py  dormant BatchRunner: claims queued jobs only when enabled

Everything is stdlib-only and import-safe (no torch / cloud SDK at import), so
it is covered by the CI smoke ladder's import + the hermetic test suite.
"""

from zo_sentinel.sft.schema import (  # noqa: F401
    JobSpec,
    JobStatus,
    ValidationResult,
    validate_job,
)

__all__ = ["JobSpec", "JobStatus", "ValidationResult", "validate_job"]
