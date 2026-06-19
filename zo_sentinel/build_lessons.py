"""
build_lessons.py -- file-based "lessons learned" index for the build loop.

Closed-loop memory PRODUCER (council 2026-06-19). When a build GIVES UP, record a
lesson keyed by the target it failed on; when a later build SUCCEEDS on that same
target, auto-resolve it. The directive_architect read-gate (separate PR, held for
chairman go-ahead) will read these files BEFORE proposing/building so it doesn't
repeat known-bad work.

DELIBERATELY FILE-BASED, zero DB load on the hot path -- mirrors state_loopback's
discipline ("zo_db_query destabilizes write_service", 2026-05-31 ops note;
goose_runner's Phase-1 feedback edge is file-based for the same reason). One small
JSON file per subject under lessons/; readers stat+read a single file, never query
the DB. A durable mesh_memory mirror is written SEPARATELY + best-effort by the
caller (goose_runner) for history/dashboards, OFF the hot path.

Pure stdlib, no import-time side effects, no hardcoded paths -- imports cleanly on
Windows/CI and is unit-testable with tmp_path. Every function is best-effort and
NEVER raises, so a lesson write can never regress a build path.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(subject_ref: str) -> str:
    """Filesystem-safe single-file name for a subject (a target filename or a
    directive_id). Collapses path separators / odd chars; never empty."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(subject_ref).strip())
    return (s or "unknown")[:120]


def _path(lessons_dir, subject_ref) -> Path:
    return Path(lessons_dir) / f"{_safe(subject_ref)}.json"


def _atomic_write(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_lesson(lessons_dir, subject_ref: str, directive_id: str, task_type: str,
                  observation: str, severity: int = 2, when: Optional[str] = None) -> dict:
    """Upsert an OPEN lesson for `subject_ref`. First occurrence creates it;
    repeats bump `recurrence` + `last_seen` (NO new file per retry -- the dedup
    that avoids write amplification on a churning directive). A subject that fails
    again after being resolved is reopened. Atomic; never raises (returns {})."""
    when = when or _now()
    p = _path(lessons_dir, subject_ref)
    try:
        Path(lessons_dir).mkdir(parents=True, exist_ok=True)
        lesson: dict = {}
        if p.exists():
            try:
                lesson = json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                lesson = {}
        lesson.setdefault("subject_ref", str(subject_ref))
        lesson.setdefault("first_seen", when)
        if lesson.get("status") == "resolved":
            lesson.pop("resolved_at", None)   # reopen
        lesson["directive_id"] = directive_id
        lesson["task_type"] = task_type
        lesson["observation"] = observation
        lesson["severity"] = int(severity)
        lesson["status"] = "open"
        lesson["recurrence"] = int(lesson.get("recurrence", 0)) + 1
        lesson["last_seen"] = when
        _atomic_write(p, lesson)
        return lesson
    except Exception:
        return {}


def resolve_lessons(lessons_dir, subject_ref: str, when: Optional[str] = None) -> bool:
    """Auto-resolve on a later green build: mark the subject's lesson resolved,
    kept as forensic history (status='resolved'). Never raises. True if resolved."""
    when = when or _now()
    p = _path(lessons_dir, subject_ref)
    try:
        if not p.exists():
            return False
        lesson = json.loads(p.read_text(encoding="utf-8")) or {}
        if lesson.get("status") == "resolved":
            return False
        lesson["status"] = "resolved"
        lesson["resolved_at"] = when
        _atomic_write(p, lesson)
        return True
    except Exception:
        return False


def format_lessons_context(lessons: List[dict], subject_ref: str) -> str:
    """Render OPEN lessons into the prompt block that goose_runner folds into a
    build task (the closed-loop READER's text -- the consumer half of this module).
    Returns '' if there are no lessons. Pure/testable so the integration text is
    unit-covered, not just sketched."""
    if not lessons:
        return ""
    lines = []
    for L in lessons[:3]:
        lines.append(
            f"  - [{L.get('task_type', 'failure')}] failed {L.get('recurrence', 1)}x: "
            f"{str(L.get('observation', ''))[:200]}")
    return ("PRIOR BUILD FAILURES on " + str(subject_ref) + " (a previous attempt "
            "GHOSTED here -- diagnose and FIX the root cause below or this build will "
            "fail the same way; do not retry blindly):\n" + "\n".join(lines))


def open_lessons_for(lessons_dir, subject_ref: str) -> List[dict]:
    """The read-gate's hot-path read: the OPEN lesson(s) for a subject as one
    small file stat+read, zero DB. [] if none or resolved. Never raises."""
    p = _path(lessons_dir, subject_ref)
    try:
        if not p.exists():
            return []
        lesson = json.loads(p.read_text(encoding="utf-8")) or {}
        return [lesson] if lesson.get("status") == "open" else []
    except Exception:
        return []
