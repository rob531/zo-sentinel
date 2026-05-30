"""
ingest.py -- file-backed ingestion queue for SFT training jobs.

A job is a single JSON file under <queue_dir>/<status>/<job_id>.json. Status is
encoded by the subdirectory so `list`/`claim` are cheap directory scans and the
queue survives process restarts with no database. Writes are atomic
(write-temp + os.replace) so a crash mid-write can't corrupt a job file.

Design goals:
  * hermetic + dependency-free (stdlib only) -> covered by the CI smoke ladder
  * durable + restart-safe -> file per job, status = directory
  * safe by default -> submit() validates and REJECTS bad jobs rather than
    silently queueing them; nothing here ever launches a GPU.

Typical flow:
    q = IngestQueue(some_dir)
    job = q.submit(spec, repo_root=REPO_ROOT)   # -> validated -> QUEUED (or REJECTED)
    # ... later, the (dormant) batch runner claims from the QUEUED dir.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from zo_sentinel.sft.schema import JobSpec, JobStatus, validate_job

# Subdirs that hold jobs by status. Terminal + working states each get a home.
_STATUS_DIRS = {
    JobStatus.RECEIVED: "received",
    JobStatus.VALIDATED: "validated",
    JobStatus.QUEUED: "queued",
    JobStatus.CLAIMED: "claimed",
    JobStatus.RUNNING: "running",
    JobStatus.DONE: "done",
    JobStatus.FAILED: "failed",
    JobStatus.REJECTED: "rejected",
}


def new_job_id() -> str:
    return "sft_" + uuid.uuid4().hex[:12]


def compute_file_digest(path: Path) -> tuple[int, str]:
    """Return (row_count, sha256_hex) for a JSONL file. Rows = non-empty lines."""
    h = hashlib.sha256()
    rows = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows += 1
    return rows, h.hexdigest()


class IngestQueue:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        for sub in _STATUS_DIRS.values():
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------

    def _dir_for(self, status: JobStatus) -> Path:
        return self.root / _STATUS_DIRS[status]

    def _path_for(self, job: JobSpec) -> Path:
        return self._dir_for(job.status) / f"{job.job_id}.json"

    def _find(self, job_id: str) -> Optional[Path]:
        for sub in _STATUS_DIRS.values():
            p = self.root / sub / f"{job_id}.json"
            if p.exists():
                return p
        return None

    # ---- atomic write ------------------------------------------------------

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _persist(self, job: JobSpec) -> Path:
        path = self._path_for(job)
        self._atomic_write(path, job.to_json())
        return path

    def _move(self, job: JobSpec, old_path: Optional[Path]) -> Path:
        """Persist job in its current-status dir and remove any prior file."""
        new_path = self._persist(job)
        if old_path and old_path.exists() and old_path != new_path:
            old_path.unlink()
        return new_path

    # ---- public API --------------------------------------------------------

    def submit(self, spec: JobSpec, *, repo_root: Optional[Path] = None,
               fill_digest: bool = True) -> JobSpec:
        """Validate + enqueue a job. On success -> QUEUED; on failure ->
        REJECTED (still persisted, so the rejection + reasons are auditable).

        If the dataset file resolves locally and fill_digest is set, the real
        row count + sha256 are computed and stamped onto the spec (and
        dataset.verified set True) before validation.
        """
        if not spec.job_id:
            spec.job_id = new_job_id()

        # Stamp real dataset digest when the file is available.
        if fill_digest and spec.dataset.train_path:
            p = Path(spec.dataset.train_path)
            if not p.is_absolute() and repo_root is not None:
                p = repo_root / spec.dataset.train_path
            if p.exists():
                rows, sha = compute_file_digest(p)
                spec.dataset.rows = rows
                spec.dataset.sha256 = sha
                spec.dataset.verified = True

        spec.mark(JobStatus.RECEIVED, "submitted")
        old = self._find(spec.job_id)
        self._move(spec, old)

        result = validate_job(spec, repo_root=repo_root)
        if result.ok:
            note = "validated"
            if result.warnings:
                note += "; warnings: " + "; ".join(result.warnings)
            old = self._find(spec.job_id)
            spec.mark(JobStatus.QUEUED, note)
            self._move(spec, old)
        else:
            old = self._find(spec.job_id)
            spec.mark(JobStatus.REJECTED, "; ".join(result.errors))
            self._move(spec, old)
        return spec

    def get(self, job_id: str) -> Optional[JobSpec]:
        p = self._find(job_id)
        if not p:
            return None
        return JobSpec.from_json(p.read_text(encoding="utf-8"))

    def list(self, status: Optional[JobStatus] = None) -> list[JobSpec]:
        statuses = [status] if status else list(_STATUS_DIRS)
        out: list[JobSpec] = []
        for st in statuses:
            d = self._dir_for(st)
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    out.append(JobSpec.from_json(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return out

    def transition(self, job_id: str, status: JobStatus, note: str = "") -> Optional[JobSpec]:
        """Move an existing job to a new status (used by the batch runner)."""
        job = self.get(job_id)
        if job is None:
            return None
        old = self._find(job_id)
        job.mark(status, note)
        self._move(job, old)
        return job

    def counts(self) -> dict[str, int]:
        return {name: len(list((self.root / name).glob("*.json")))
                for name in _STATUS_DIRS.values()}
