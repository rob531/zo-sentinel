"""
corpus.py -- the distillation corpus store: segment-per-batch, quarantine for
poisoned teacher returns, append-only manifest.

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §2.3, §3.4.

The 2026-04-29 failure this exists to make impossible: "MiniMax returned
empty" (GENERATION_FAILURES.md) with nothing guarding the corpus -- an empty
or undersized teacher return must be QUARANTINED, never appended, and the
rejection must be loud and durable (GC-5: no silent drops).

Layout under the corpus root:
    segments/<batch_id>.jsonl    one atomic file per accepted batch
    quarantine/<batch_id>.json   rejected returns, with the reason + payload
    manifest.jsonl               append-only ledger: one row per decision
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Canonical verdict vocabulary (documented 7-term set; selection, not
# redefinition -- taxonomy is HELD). The dialect gate of design §1.4: a
# teacher row whose verdict is outside this set is a validation error.
CANONICAL_VERDICTS = frozenset({
    "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT",
})

MIN_FRACTION = 0.5   # a teacher return smaller than this share of the batch is rejected


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AppendResult:
    accepted: bool
    batch_id: str
    rows: int = 0
    reason: str = ""
    duplicate: bool = False
    segment_path: Optional[str] = None
    errors: List[str] = field(default_factory=list)


def validate_row(row: Dict[str, Any]) -> List[str]:
    """Structural + dialect validation of one teacher-labelled corpus row."""
    errs: List[str] = []
    msgs = row.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return ["'messages' must be a non-empty list"]
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or "role" not in m or "content" not in m:
            errs.append(f"message[{i}] needs role+content")
        elif not str(m.get("content", "")).strip():
            errs.append(f"message[{i}] has empty content")
    assistant = next((m for m in reversed(msgs)
                      if isinstance(m, dict) and m.get("role") == "assistant"), None)
    if assistant is None:
        errs.append("no assistant message (the label) present")
    else:
        try:
            payload = json.loads(assistant.get("content", ""))
            verdict = payload.get("verdict", "")
            if verdict not in CANONICAL_VERDICTS:
                errs.append(
                    f"verdict {verdict!r} outside the canonical 7-term set "
                    "(dialect gate, design S1.4)")
        except (ValueError, TypeError):
            errs.append("assistant content is not a JSON label payload")
    return errs


class CorpusStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        (self.root / "segments").mkdir(parents=True, exist_ok=True)
        (self.root / "quarantine").mkdir(parents=True, exist_ok=True)

    # ---- paths ---------------------------------------------------------------

    def segment_path(self, batch_id: str) -> Path:
        return self.root / "segments" / f"{batch_id}.jsonl"

    def quarantine_path(self, batch_id: str) -> Path:
        return self.root / "quarantine" / f"{batch_id}.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.jsonl"

    # ---- internals -----------------------------------------------------------

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

    def _manifest_append(self, entry: Dict[str, Any]) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    # ---- public API ------------------------------------------------------------

    def append_batch(self, batch_id: str, rows: List[Dict[str, Any]], *,
                     expected_count: int, teacher: str = "",
                     server_ids: Optional[List[str]] = None,
                     min_fraction: float = MIN_FRACTION) -> AppendResult:
        """Validate + append one teacher-labelled batch as a segment file.

        Rejections quarantine the payload and append a manifest row; they
        NEVER touch segments/. Idempotent: an existing segment for batch_id is
        a no-op duplicate.
        """
        if self.segment_path(batch_id).exists():
            return AppendResult(accepted=True, batch_id=batch_id, duplicate=True,
                                rows=0, reason="segment already exists (no-op)")

        errors: List[str] = []
        if not rows:
            # THE 2026-04-29 pole: an empty teacher return.
            errors.append("teacher returned EMPTY (0 rows) -- the 2026-04-29 failure")
        elif expected_count > 0 and len(rows) < min_fraction * expected_count:
            errors.append(
                f"teacher returned {len(rows)}/{expected_count} rows "
                f"(< min_fraction {min_fraction})")
        else:
            for i, row in enumerate(rows):
                for e in validate_row(row):
                    errors.append(f"row {i}: {e}")
                if len(errors) >= 10:
                    errors.append("... (further errors elided)")
                    break

        if errors:
            self._atomic_write(self.quarantine_path(batch_id), json.dumps({
                "batch_id": batch_id, "at": _now_iso(), "teacher": teacher,
                "expected_count": expected_count, "returned": len(rows),
                "errors": errors,
                "payload_head": rows[:3],
            }, indent=2, default=str))
            self._manifest_append({
                "batch_id": batch_id, "at": _now_iso(), "decision": "quarantined",
                "rows": 0, "returned": len(rows), "expected": expected_count,
                "teacher": teacher, "errors": errors[:5],
            })
            return AppendResult(accepted=False, batch_id=batch_id,
                                reason="quarantined", errors=errors)

        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        seg = self.segment_path(batch_id)
        self._atomic_write(seg, text)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        ids_sha = hashlib.sha256(
            ",".join(sorted(server_ids or [])).encode("utf-8")).hexdigest()[:16]
        self._manifest_append({
            "batch_id": batch_id, "at": _now_iso(), "decision": "appended",
            "rows": len(rows), "expected": expected_count, "teacher": teacher,
            "segment_sha256": sha, "server_ids_sha": ids_sha,
        })
        return AppendResult(accepted=True, batch_id=batch_id, rows=len(rows),
                            segment_path=str(seg), reason="appended")

    def record_failed_out(self, batch_id: str, *, attempts: int, reason: str) -> None:
        """A batch that exhausted MAX_ATTEMPTS: durable manifest row, no segment.
        The caller advances the watermark and emits the mesh_event alert."""
        self._manifest_append({
            "batch_id": batch_id, "at": _now_iso(), "decision": "failed_out",
            "attempts": attempts, "reason": reason, "rows": 0,
        })

    # ---- stats ------------------------------------------------------------------

    def manifest_entries(self) -> List[Dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        out = []
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def total_rows(self) -> int:
        return sum(e.get("rows", 0) for e in self.manifest_entries()
                   if e.get("decision") == "appended")

    def manifest_sha(self) -> str:
        """Deterministic digest of the accepted-corpus state. Used as the
        retrain job_id key (same corpus -> same job_id -> dedupe)."""
        accepted = sorted(
            (e["batch_id"], e.get("segment_sha256", ""))
            for e in self.manifest_entries() if e.get("decision") == "appended")
        material = json.dumps(accepted, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def snapshot_path(self, sha: Optional[str] = None) -> Path:
        """The (prospective) training snapshot file for a corpus state. Pure
        path arithmetic -- computing it writes nothing, so dry-run can NAME
        the snapshot it would train on without creating it."""
        sha = sha or self.manifest_sha()
        return self.root / "snapshots" / f"corpus_{sha[:12]}.jsonl"

    def write_snapshot(self) -> Path:
        """Materialise the accepted corpus as one training JSONL (sorted
        segment concatenation, atomic). Idempotent: the snapshot is keyed on
        the corpus state, so an existing file for this state is reused."""
        sha = self.manifest_sha()
        path = self.snapshot_path(sha)
        if path.exists():
            return path
        accepted = sorted(e["batch_id"] for e in self.manifest_entries()
                          if e.get("decision") == "appended")
        parts = [self.segment_path(b).read_text(encoding="utf-8") for b in accepted]
        self._atomic_write(path, "".join(parts))
        return path
