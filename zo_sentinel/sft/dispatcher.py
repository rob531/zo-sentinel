"""
dispatcher.py -- the REAL dispatcher, behind the single documented flag.

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §3.5.

`ZO_SFT_LABEL_LOOP_ARMED=1` is the ONE switch an operator (the GPU lane)
opens. `build_runner()` reads it and returns a BatchRunner that is either

  * armed:   enabled=True + ShellDispatcher (shells out to the sft repo's
             dispatch script, e.g. dispatch_vast_v3.sh), or
  * dormant: the stock dormant BatchRunner (NoopDispatcher, latches closed).

The two legacy latches (SFT_BATCH_ENABLED / .batch_enabled, and explicit
dispatcher wiring) still work underneath -- defense in depth, not a second
interface. ShellDispatcher additionally refuses to launch on its own when the
flag is closed, and honours per-job dispatch.dry_run, so even a hand-wired
armed runner cannot start a GPU batch without BOTH the flag and a job that
asks for a real run.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from zo_sentinel.sft.batch_runner import BatchRunner, DispatchOutcome, NoopDispatcher
from zo_sentinel.sft.ingest import IngestQueue
from zo_sentinel.sft.schema import JobSpec

# The single documented flag (design §3.5). One flag, one doc line, one grep.
ARM_FLAG = "ZO_SFT_LABEL_LOOP_ARMED"
# Where the real dispatch command lives (the sft repo's script). Required when
# armed; refusing loudly beats guessing a path.
DISPATCH_CMD_ENV = "ZO_SFT_DISPATCH_CMD"

_TRUTHY = ("1", "true", "yes")


def is_armed(env: Optional[dict] = None) -> bool:
    e = os.environ if env is None else env
    return (e.get(ARM_FLAG, "") or "").strip().lower() in _TRUTHY


class ShellDispatcher:
    """Dispatches a job by shelling out to the configured dispatch command
    with the job's JSON file as its argument (the dispatch_vast_v3.sh /
    install_sky_dispatcher.sh driving shape).

    launched=True is returned ONLY when the command actually ran and exited 0.
    Refuses (ok=False, launched=False) when the arm flag is closed or the
    command is unconfigured; records a dry-run when the job asks for one.
    """
    name = "shell"

    def __init__(self, command: Optional[str] = None, *, env: Optional[dict] = None,
                 timeout: int = 300):
        self._env = env  # injectable for tests; None -> os.environ
        self.command = command or (os.environ if env is None else env).get(
            DISPATCH_CMD_ENV, "")
        self.timeout = timeout

    def dispatch(self, job: JobSpec) -> DispatchOutcome:
        if not is_armed(self._env):
            return DispatchOutcome(
                ok=False, backend=self.name, launched=False,
                detail=f"REFUSED: {ARM_FLAG} is not set -- real dispatch is HELD")
        if job.dispatch.dry_run:
            return DispatchOutcome(
                ok=True, backend=self.name, launched=False,
                detail=(f"DRY-RUN (job asked for it): would exec "
                        f"{self.command or '<unconfigured>'} for {job.job_id} "
                        f"dataset={job.dataset.train_path} rows={job.dataset.rows}"))
        if not self.command:
            return DispatchOutcome(
                ok=False, backend=self.name, launched=False,
                detail=f"REFUSED: {DISPATCH_CMD_ENV} is not configured")
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix=f"{job.job_id}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(job.to_json())
            proc = subprocess.run(
                [self.command, tmp], capture_output=True, text=True,
                timeout=self.timeout)
            if proc.returncode == 0:
                return DispatchOutcome(
                    ok=True, backend=self.name, launched=True,
                    detail=f"launched via {self.command}: {proc.stdout[-500:]}")
            return DispatchOutcome(
                ok=False, backend=self.name, launched=False,
                detail=(f"dispatch command rc={proc.returncode}: "
                        f"{(proc.stderr or proc.stdout)[-500:]}"))
        except (OSError, subprocess.SubprocessError) as e:
            return DispatchOutcome(ok=False, backend=self.name, launched=False,
                                   detail=f"dispatch error: {e}")
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass


def build_runner(queue: IngestQueue, *, env: Optional[dict] = None) -> BatchRunner:
    """THE factory. Flag open -> armed runner with the real dispatcher; flag
    closed -> the stock dormant runner. This is the only place the label loop
    constructs a BatchRunner, so the arming surface stays one line wide."""
    if is_armed(env):
        return BatchRunner(queue, dispatcher=ShellDispatcher(env=env), enabled=True)
    return BatchRunner(queue, dispatcher=NoopDispatcher(), enabled=None)
