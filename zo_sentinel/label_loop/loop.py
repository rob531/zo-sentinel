"""
loop.py -- the label-loop orchestrator: trigger -> teacher -> corpus -> retrain
enqueue, with DRY-RUN as the default posture.

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md §3.

Every external effect is injected (registry count, batch fetch, teacher call,
event emit), so the tick is hermetic under test and the production wiring is
one thin adapter. Nothing in this module dispatches a GPU job: the farthest it
reaches is IngestQueue.submit() -- a file write -- and in dry-run mode it only
LOGS what it would enqueue.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from zo_sentinel.label_loop.corpus import CorpusStore, validate_row
from zo_sentinel.label_loop.trigger import Batch, RowTrigger
from zo_sentinel.sft.ingest import IngestQueue
from zo_sentinel.sft.schema import DatasetRef, DispatchSpec, JobSpec, StudentSpec

log = logging.getLogger("label_loop")

RETRAIN_ROW_THRESHOLD = 2000   # validated corpus growth that triggers a retrain
DEFAULT_BASE_MODEL = "qwen2.5-3b"
DEFAULT_RECIPE = "sft_v3_dpo.yaml"
DEFAULT_ACCELERATORS = {"RTXA5000": 1}

# Type aliases for the injected effects.
FetchFn = Callable[[Batch], List[Dict[str, Any]]]          # batch -> server rows
TeacherFn = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]  # servers -> corpus rows
EmitFn = Callable[[str, Dict[str, Any]], None]             # (event_type, payload)


@dataclass
class TickReport:
    dry_run: bool
    registry_count: int = 0
    watermark_before: int = 0
    watermark_after: int = 0
    batches_seen: int = 0
    batches_appended: int = 0
    batches_quarantined: int = 0
    batches_failed_out: int = 0
    would_enqueue: Optional[str] = None   # job_id the tick WOULD submit (dry-run)
    enqueued: Optional[str] = None        # job_id actually submitted
    notes: List[str] = field(default_factory=list)


def _log_note(report: TickReport, msg: str) -> None:
    report.notes.append(msg)
    log.info(msg)


def build_retrain_spec(corpus: CorpusStore, *, init_adapter: Optional[str],
                       output_name: str) -> JobSpec:
    """Deterministic retrain JobSpec: the job_id is keyed on the accepted
    corpus state, so the same corpus can never enqueue twice. train_path is
    the PROSPECTIVE snapshot path (write nothing here -- dry-run must be able
    to name it without creating it; the live path materialises it first)."""
    sha = corpus.manifest_sha()
    return JobSpec(
        job_id=f"sft_{sha[:12]}",
        method="sft",
        student=StudentSpec(base_model=DEFAULT_BASE_MODEL,
                            init_adapter=init_adapter, output_name=output_name),
        dataset=DatasetRef(
            train_path=str(corpus.snapshot_path(sha)),
            fmt="messages",
            rows=corpus.total_rows(),
            sha256=sha,
            manifest_path=str(corpus.manifest_path),
        ),
        resources={"accelerators": dict(DEFAULT_ACCELERATORS)},
        dispatch=DispatchSpec(backend="vast", dry_run=False, recipe=DEFAULT_RECIPE),
        provenance={"source": "label_loop", "corpus_sha": sha},
    )


def run_once(
    *,
    registry_count: int,
    fetch_fn: FetchFn,
    teacher_fn: TeacherFn,
    state_path: Path | str,
    corpus_dir: Path | str,
    queue_dir: Path | str,
    dry_run: bool = True,
    emit_fn: Optional[EmitFn] = None,
    retrain_threshold: int = RETRAIN_ROW_THRESHOLD,
    rows_at_last_job: int = 0,
    init_adapter: Optional[str] = "student_v1",
    output_name: str = "student_v2",
    max_batches: int = 5,
) -> TickReport:
    """One tick of the label loop. Idempotent and crash-safe at every stage
    (see design §3.3); DRY-RUN (the default) performs teacher/corpus work only
    for batches it is given and LOGS what it would enqueue without enqueuing.

    In dry-run the corpus is also left untouched: batches are evaluated
    (fetch + teacher + validation) but nothing is appended and the watermark
    does not move -- a pure rehearsal.
    """
    emit = emit_fn or (lambda t, p: log.warning("mesh_event %s %s", t, p))
    trigger = RowTrigger(state_path)
    corpus = CorpusStore(corpus_dir)
    report = TickReport(dry_run=dry_run, registry_count=registry_count,
                        watermark_before=trigger.watermark)

    batches = trigger.pending_batches(registry_count)[:max_batches]
    report.batches_seen = len(batches)
    if not batches:
        _log_note(report, f"no full 1k batch pending (watermark={trigger.watermark}, "
                          f"count={registry_count})")

    for batch in batches:
        servers = fetch_fn(batch)
        rows = teacher_fn(servers)
        if dry_run:
            n_bad = sum(1 for r in rows if validate_row(r))
            _log_note(report,
                      f"DRY-RUN: batch {batch.batch_id} ({batch.start},{batch.end}] "
                      f"-> teacher returned {len(rows)} rows ({n_bad} invalid); "
                      f"WOULD append + advance watermark")
            continue

        attempts = trigger.record_attempt(batch)
        result = corpus.append_batch(
            batch.batch_id, rows, expected_count=batch.size,
            server_ids=[str(s.get("server_id", "")) for s in servers])
        if result.accepted:
            trigger.resolve(batch, outcome="appended")
            report.batches_appended += 1
            _log_note(report, f"batch {batch.batch_id}: appended {result.rows} rows")
        else:
            report.batches_quarantined += 1
            _log_note(report, f"batch {batch.batch_id}: QUARANTINED "
                              f"(attempt {attempts}): {result.errors[:2]}")
            if attempts >= trigger.max_attempts:
                corpus.record_failed_out(batch.batch_id, attempts=attempts,
                                         reason="; ".join(result.errors[:3]))
                trigger.resolve(batch, outcome="failed_out")
                report.batches_failed_out += 1
                emit("label_loop_batch_failed", {
                    "batch_id": batch.batch_id, "attempts": attempts,
                    "errors": result.errors[:3]})
            else:
                # Not resolved -> the watermark stays; the next tick retries.
                break

    # ---- retrain check -------------------------------------------------------
    grown = corpus.total_rows() - rows_at_last_job
    if grown >= retrain_threshold:
        spec = build_retrain_spec(corpus, init_adapter=init_adapter,
                                  output_name=output_name)
        queue = IngestQueue(queue_dir)
        if queue.get(spec.job_id) is not None:
            _log_note(report, f"retrain job {spec.job_id} already exists -- skip "
                              "(idempotent enqueue)")
        elif dry_run:
            report.would_enqueue = spec.job_id
            _log_note(report,
                      f"DRY-RUN: WOULD enqueue retrain {spec.job_id} "
                      f"(corpus rows={spec.dataset.rows}, grown={grown}, "
                      f"recipe={spec.dispatch.recipe}, "
                      f"accelerators={spec.resources['accelerators']}) -- NOT enqueuing")
        else:
            corpus.write_snapshot()
            submitted = queue.submit(spec)
            report.enqueued = submitted.job_id
            _log_note(report, f"enqueued retrain {submitted.job_id} "
                              f"status={submitted.status.value}")
    else:
        _log_note(report, f"corpus grew {grown} rows since last job "
                          f"(< {retrain_threshold}); no retrain")

    report.watermark_after = RowTrigger(state_path).watermark
    return report
