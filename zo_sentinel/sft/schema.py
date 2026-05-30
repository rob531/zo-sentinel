"""
schema.py -- the SFT training-job contract.

A JobSpec is the unit the ingestion layer receives. It is deliberately a plain
dataclass with explicit (de)serialization and a pure validator -- no pydantic,
no torch, no cloud SDK -- so it imports anywhere and is cheap to test.

The dataset formats mirror what zomesh-sentinel-sft actually consumes:
  * "messages"   chat-format SFT rows: {"messages": [{role, content}...], ...}
  * "preference" DPO rows: {"chosen": ..., "rejected": ..., ["prompt": ...]}
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class JobStatus(str, Enum):
    """Lifecycle of an ingested job. Forward-only except FAILED/REJECTED."""
    RECEIVED = "received"      # accepted, not yet validated
    VALIDATED = "validated"    # passed schema + dataset checks
    QUEUED = "queued"          # eligible for the batch runner to claim
    CLAIMED = "claimed"        # a runner has taken it (dormant unless enabled)
    RUNNING = "running"        # batch in flight (set by the dispatcher)
    DONE = "done"              # batch finished, adapter produced
    FAILED = "failed"          # ran but failed
    REJECTED = "rejected"      # failed validation; never queued


ALLOWED_METHODS = {"sft", "dpo", "grpo"}
ALLOWED_DATASET_FORMATS = {"messages", "preference"}
ALLOWED_DISPATCH_BACKENDS = {"noop", "runpod", "vast", "skypilot"}

_JOB_ID_RE = re.compile(r"^sft_[0-9a-f]{8,}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DatasetRef:
    """Where the training data lives + what we know about it.

    train_path may be a repo-relative path (verifiable here) or a remote ref
    (e.g. an HF dataset / git-lfs pointer) that can't be opened at ingest time;
    in that case `verified` stays False and rows/sha256 are taken as declared.
    """
    train_path: str
    fmt: str = "messages"              # one of ALLOWED_DATASET_FORMATS
    rows: int = 0
    sha256: str = ""
    manifest_path: Optional[str] = None
    verified: bool = False             # set True once contents are validated


@dataclass
class StudentSpec:
    base_model: str
    init_adapter: Optional[str] = None   # e.g. "student_v1" to continue from
    output_name: str = ""                # adapter name to produce


@dataclass
class DispatchSpec:
    backend: str = "noop"                # ALLOWED_DISPATCH_BACKENDS
    dry_run: bool = True                 # safety default: never really launch
    recipe: str = ""                     # yaml recipe name in the sft repo


@dataclass
class JobSpec:
    job_id: str
    method: str                          # ALLOWED_METHODS
    student: StudentSpec
    dataset: DatasetRef
    resources: dict[str, Any] = field(default_factory=dict)   # {accelerators:{...},...}
    dispatch: DispatchSpec = field(default_factory=DispatchSpec)
    priority: int = 100                  # lower = sooner
    provenance: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.RECEIVED
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    history: list[dict[str, str]] = field(default_factory=list)

    # ---- (de)serialization -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobSpec":
        student = d.get("student", {})
        dataset = d.get("dataset", {})
        dispatch = d.get("dispatch", {})
        return cls(
            job_id=d["job_id"],
            method=d["method"],
            student=StudentSpec(
                base_model=student.get("base_model", ""),
                init_adapter=student.get("init_adapter"),
                output_name=student.get("output_name", ""),
            ),
            dataset=DatasetRef(
                train_path=dataset.get("train_path", ""),
                fmt=dataset.get("fmt", "messages"),
                rows=int(dataset.get("rows", 0) or 0),
                sha256=dataset.get("sha256", ""),
                manifest_path=dataset.get("manifest_path"),
                verified=bool(dataset.get("verified", False)),
            ),
            resources=d.get("resources", {}) or {},
            dispatch=DispatchSpec(
                backend=dispatch.get("backend", "noop"),
                dry_run=bool(dispatch.get("dry_run", True)),
                recipe=dispatch.get("recipe", ""),
            ),
            priority=int(d.get("priority", 100)),
            provenance=d.get("provenance", {}) or {},
            status=JobStatus(d.get("status", "received")),
            created_at=d.get("created_at", _now_iso()),
            updated_at=d.get("updated_at", _now_iso()),
            history=d.get("history", []) or [],
        )

    @classmethod
    def from_json(cls, text: str) -> "JobSpec":
        return cls.from_dict(json.loads(text))

    # ---- status transitions ------------------------------------------------

    def mark(self, status: JobStatus, note: str = "") -> "JobSpec":
        """Record a status transition with a timestamped history entry."""
        self.status = status
        self.updated_at = _now_iso()
        self.history.append({"at": self.updated_at, "status": status.value, "note": note})
        return self


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


# ---------------------------------------------------------------------------
# Validation -- pure, hermetic. Verifies structure always; verifies dataset
# CONTENTS when the file is locally available (else defers with a warning).
# ---------------------------------------------------------------------------

def _required_keys_for_format(fmt: str) -> tuple[set[str], str]:
    if fmt == "messages":
        return {"messages"}, "chat rows need a 'messages' list of {role, content}"
    if fmt == "preference":
        return {"chosen", "rejected"}, "DPO rows need 'chosen' and 'rejected'"
    return set(), ""


def validate_dataset_file(path: Path, fmt: str, *, max_scan: int = 0) -> ValidationResult:
    """Validate a JSONL training file's structure. Scans every line unless
    max_scan>0. Returns counts via warnings is not done here -- the caller
    (ingest) records rows/sha. This only asserts well-formedness."""
    res = ValidationResult(ok=True)
    req, hint = _required_keys_for_format(fmt)
    if fmt not in ALLOWED_DATASET_FORMATS:
        res.ok = False
        res.errors.append(f"unknown dataset format {fmt!r}")
        return res
    if not path.exists():
        res.ok = False
        res.errors.append(f"dataset file not found: {path}")
        return res

    n = 0
    bad = 0
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            n += 1
            if max_scan and n > max_scan:
                break
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                bad += 1
                if bad <= 3:
                    res.errors.append(f"line {i+1}: invalid JSON: {e}")
                continue
            missing = req - set(row.keys())
            if missing:
                bad += 1
                if bad <= 3:
                    res.errors.append(f"line {i+1}: missing {sorted(missing)} ({hint})")
            elif fmt == "messages":
                msgs = row.get("messages")
                if not isinstance(msgs, list) or not msgs:
                    bad += 1
                    if bad <= 3:
                        res.errors.append(f"line {i+1}: 'messages' must be a non-empty list")
                elif not all(isinstance(m, dict) and "role" in m and "content" in m for m in msgs):
                    bad += 1
                    if bad <= 3:
                        res.errors.append(f"line {i+1}: each message needs role+content")
    if n == 0:
        res.ok = False
        res.errors.append("dataset file is empty")
    if bad:
        res.ok = False
        res.warnings.append(f"{bad}/{n} rows failed structure checks")
    return res


def validate_job(spec: JobSpec, *, repo_root: Optional[Path] = None,
                 scan_dataset: bool = True) -> ValidationResult:
    """Validate a JobSpec end-to-end. Structural checks always run; dataset
    content checks run when the file resolves under repo_root (or as an
    absolute path). Remote/absent datasets defer with a warning, not an error.
    """
    res = ValidationResult(ok=True)

    if not _JOB_ID_RE.match(spec.job_id or ""):
        res.errors.append(f"job_id must match {_JOB_ID_RE.pattern!r}, got {spec.job_id!r}")
    if spec.method not in ALLOWED_METHODS:
        res.errors.append(f"method must be one of {sorted(ALLOWED_METHODS)}, got {spec.method!r}")
    if not spec.student.base_model:
        res.errors.append("student.base_model is required")
    if spec.dataset.fmt not in ALLOWED_DATASET_FORMATS:
        res.errors.append(f"dataset.fmt must be one of {sorted(ALLOWED_DATASET_FORMATS)}")
    if spec.dispatch.backend not in ALLOWED_DISPATCH_BACKENDS:
        res.errors.append(f"dispatch.backend must be one of {sorted(ALLOWED_DISPATCH_BACKENDS)}")
    if not isinstance(spec.resources, dict) or not spec.resources.get("accelerators"):
        res.errors.append("resources.accelerators is required (e.g. {'RTXA5000': 1})")
    if spec.priority < 0:
        res.errors.append("priority must be >= 0")

    # Dataset contents (best-effort, hermetic)
    if scan_dataset and spec.dataset.train_path:
        p = Path(spec.dataset.train_path)
        if not p.is_absolute() and repo_root is not None:
            p = repo_root / spec.dataset.train_path
        if p.exists():
            dres = validate_dataset_file(p, spec.dataset.fmt)
            res.errors.extend(dres.errors)
            res.warnings.extend(dres.warnings)
        else:
            res.warnings.append(
                f"dataset not locally resolvable ({spec.dataset.train_path}); "
                "structural validation deferred to dispatch time")

    res.ok = not res.errors
    return res
