"""
batch_runner.py -- the DORMANT batch runner.

This is the piece that will, *when the student model is ready for batch
running*, pull QUEUED jobs and hand them to the SFT training pipeline. Until
then it stays dormant by default and refuses to claim or dispatch.

Two independent safety latches, both must be open to dispatch for real:
  1. enabled        -- the runner is activated (env SFT_BATCH_ENABLED=1, or a
                       `.batch_enabled` sentinel file in the queue dir, or
                       BatchRunner(enabled=True)). Default: dormant.
  2. dispatcher     -- defaults to NoopDispatcher, which only RECORDS intent
                       (dry-run) and never launches a GPU. A real dispatcher is
                       a separate, explicit wiring step.

So importing/using this module can never start a cloud job by accident. The
shape mirrors how zomesh-sentinel-sft is driven (dispatch_vast_v3.sh /
install_sky_dispatcher.sh): a real RunpodDispatcher would shell out to those,
but only once someone wires it in and flips both latches.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from zo_sentinel.sft.ingest import IngestQueue
from zo_sentinel.sft.schema import JobSpec, JobStatus

SENTINEL_NAME = ".batch_enabled"


class Dispatcher(Protocol):
    """A dispatch backend. Real ones launch cloud GPU batches; the default one
    only records intent."""
    name: str

    def dispatch(self, job: JobSpec) -> "DispatchOutcome": ...


@dataclass
class DispatchOutcome:
    ok: bool
    backend: str
    detail: str = ""
    launched: bool = False   # True only if a real batch was started


class NoopDispatcher:
    """Records what WOULD be launched. Never starts a GPU job. This is the
    default so the runner is safe to exercise in tests and CI."""
    name = "noop"

    def dispatch(self, job: JobSpec) -> DispatchOutcome:
        accel = (job.resources or {}).get("accelerators", {})
        recipe = job.dispatch.recipe or "<no recipe>"
        return DispatchOutcome(
            ok=True, backend=self.name, launched=False,
            detail=(f"DRY-RUN: would run {job.method} job {job.job_id} "
                    f"recipe={recipe} accelerators={accel} "
                    f"dataset={job.dataset.train_path} rows={job.dataset.rows}"),
        )


class BatchRunner:
    """Claims QUEUED jobs and dispatches them -- but only when activated."""

    def __init__(self, queue: IngestQueue, *, dispatcher: Optional[Dispatcher] = None,
                 enabled: Optional[bool] = None):
        self.queue = queue
        self.dispatcher: Dispatcher = dispatcher or NoopDispatcher()
        self._enabled_override = enabled

    # ---- activation latch --------------------------------------------------

    def is_enabled(self) -> bool:
        """Dormant unless explicitly activated by ANY of:
        constructor enabled=True, env SFT_BATCH_ENABLED=1, or a sentinel file."""
        if self._enabled_override is not None:
            return self._enabled_override
        if os.environ.get("SFT_BATCH_ENABLED", "").strip() in ("1", "true", "yes"):
            return True
        return (self.queue.root / SENTINEL_NAME).exists()

    def activation_reason(self) -> str:
        if self._enabled_override is True:
            return "constructor enabled=True"
        if self._enabled_override is False:
            return "constructor enabled=False (forced dormant)"
        if os.environ.get("SFT_BATCH_ENABLED", "").strip() in ("1", "true", "yes"):
            return "env SFT_BATCH_ENABLED"
        if (self.queue.root / SENTINEL_NAME).exists():
            return f"sentinel {SENTINEL_NAME}"
        return "dormant (no activation latch open)"

    # ---- claiming ----------------------------------------------------------

    def _next_queued(self) -> Optional[JobSpec]:
        queued = self.queue.list(JobStatus.QUEUED)
        if not queued:
            return None
        # lowest priority value first, then oldest created_at
        queued.sort(key=lambda j: (j.priority, j.created_at))
        return queued[0]

    def claim_next(self) -> Optional[JobSpec]:
        """Claim the highest-priority queued job. Returns None when dormant or
        when the queue is empty. A claimed job moves QUEUED -> CLAIMED."""
        if not self.is_enabled():
            return None
        nxt = self._next_queued()
        if nxt is None:
            return None
        return self.queue.transition(nxt.job_id, JobStatus.CLAIMED,
                                     f"claimed by batch_runner ({self.dispatcher.name})")

    def run_once(self) -> Optional[DispatchOutcome]:
        """Claim + dispatch a single job. No-op (returns None) while dormant.

        With the default NoopDispatcher this records a dry-run and leaves the
        job CLAIMED (not RUNNING) -- nothing is actually launched. A real
        dispatcher that returns launched=True advances the job to RUNNING.
        """
        job = self.claim_next()
        if job is None:
            return None
        outcome = self.dispatcher.dispatch(job)
        if outcome.launched and outcome.ok:
            self.queue.transition(job.job_id, JobStatus.RUNNING, outcome.detail)
        elif not outcome.ok:
            self.queue.transition(job.job_id, JobStatus.FAILED, outcome.detail)
        else:
            # dry-run: leave CLAIMED, just append the recorded intent
            self.queue.transition(job.job_id, JobStatus.CLAIMED, outcome.detail)
        return outcome

    def drain(self, limit: int = 100) -> list[DispatchOutcome]:
        """Process up to `limit` queued jobs. Empty list while dormant."""
        outcomes: list[DispatchOutcome] = []
        for _ in range(limit):
            o = self.run_once()
            if o is None:
                break
            outcomes.append(o)
        return outcomes


def queue_status_report(queue_dir: Path | str) -> dict:
    """Convenience: a one-call snapshot of the queue + activation state. Used
    by ops/CLI to answer 'is the SFT intake healthy and is it dormant?'"""
    q = IngestQueue(queue_dir)
    runner = BatchRunner(q)
    return {
        "queue_dir": str(q.root),
        "counts": q.counts(),
        "enabled": runner.is_enabled(),
        "activation": runner.activation_reason(),
        "dispatcher": runner.dispatcher.name,
    }
