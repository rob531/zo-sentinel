"""
trigger.py -- the 1k-registry-row trigger with a durable watermark.

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §3.2-§3.3.

mcp_server_registry is append-only (architecture constraint), so its row count
is monotonic; the watermark is a count, batches are (start, end] windows of
BATCH_SIZE, and the batch id is a deterministic hash of the window -- the same
window always produces the same id, which is what makes every downstream stage
idempotent.

The watermark advances only on batch RESOLUTION (corpus-appended, or
failed-out after MAX_ATTEMPTS), never on dispatch of work.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

BATCH_SIZE = 1000
MAX_ATTEMPTS = 3
CORPUS_VERSION = "v4"


@dataclass(frozen=True)
class Batch:
    start: int   # exclusive
    end: int     # inclusive

    @property
    def batch_id(self) -> str:
        material = f"{CORPUS_VERSION}:{self.start}:{self.end}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @property
    def size(self) -> int:
        return self.end - self.start


class RowTrigger:
    """Watermark over the registry row count, persisted as a single JSON file
    written atomically (write-temp + os.replace, the ingest.py discipline)."""

    def __init__(self, state_path: Path | str, *, batch_size: int = BATCH_SIZE,
                 max_attempts: int = MAX_ATTEMPTS):
        self.state_path = Path(state_path)
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self._state = self._load()

    # ---- state -------------------------------------------------------------

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # A corrupt state file must not silently reset the watermark to
                # zero (that would re-teacher the whole corpus). Refuse loudly.
                raise RuntimeError(
                    f"label_loop state file unreadable: {self.state_path} -- "
                    "refusing to guess a watermark; inspect or restore it")
        return {"watermark": 0, "attempts": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, sort_keys=True)
            os.replace(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ---- public API ---------------------------------------------------------

    @property
    def watermark(self) -> int:
        return int(self._state.get("watermark", 0))

    def pending_batches(self, current_count: int) -> List[Batch]:
        """Full windows of batch_size between the watermark and current_count.
        A partial tail (< batch_size rows) is NOT a batch -- it waits."""
        out: List[Batch] = []
        start = self.watermark
        while start + self.batch_size <= current_count:
            out.append(Batch(start=start, end=start + self.batch_size))
            start += self.batch_size
        return out

    def attempts(self, batch: Batch) -> int:
        return int(self._state.get("attempts", {}).get(batch.batch_id, 0))

    def record_attempt(self, batch: Batch) -> int:
        """Increment and persist the attempt counter. Returns the new count."""
        att = self._state.setdefault("attempts", {})
        att[batch.batch_id] = att.get(batch.batch_id, 0) + 1
        self._save()
        return att[batch.batch_id]

    def resolve(self, batch: Batch, *, outcome: str) -> None:
        """Advance the watermark past a RESOLVED batch (appended or
        failed-out). Batches resolve strictly in order; resolving a batch that
        does not start at the watermark is a programming error, not a state to
        paper over."""
        if batch.start != self.watermark:
            raise ValueError(
                f"batch {batch.batch_id} starts at {batch.start}, watermark is "
                f"{self.watermark} -- batches resolve in order")
        self._state["watermark"] = batch.end
        self._state.setdefault("attempts", {}).pop(batch.batch_id, None)
        self._state.setdefault("resolved", []).append(
            {"batch_id": batch.batch_id, "start": batch.start, "end": batch.end,
             "outcome": outcome})
        self._save()
