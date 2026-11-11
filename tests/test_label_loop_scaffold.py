"""Hermetic two-pole tests for the label-loop scaffold.

Design of record: docs/SCORER_DISCRIMINATION_DESIGN_2026-10-09.md.

No torch, no cloud, no GPU, no bus -- pure filesystem + injected effects.
The poles this file pins:

  * gate:   degenerate scores (the 70-prior collapse) -> RED;
            discriminating, well-separated scores -> GREEN.
  * corpus: an EMPTY teacher return (the 2026-04-29 "MiniMax returned empty"
            case) -> quarantined, corpus untouched; a good batch -> appended;
            a replay of the same batch -> no-op duplicate.
  * loop:   DRY-RUN logs what it WOULD enqueue and enqueues NOTHING;
            live tick enqueues exactly once (idempotent on re-run).
  * flag:   ZO_SFT_LABEL_LOOP_ARMED closed -> dormant runner + ShellDispatcher
            refuses; open -> armed runner with the real dispatcher (which
            still honours per-job dry_run and never launches here).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.label_loop.gate import evaluate_scores  # noqa: E402
from zo_sentinel.label_loop.trigger import Batch, RowTrigger  # noqa: E402
from zo_sentinel.label_loop.corpus import CorpusStore  # noqa: E402
from zo_sentinel.label_loop.loop import run_once  # noqa: E402
from zo_sentinel.sft.batch_runner import BatchRunner  # noqa: E402
from zo_sentinel.sft.dispatcher import (  # noqa: E402
    ARM_FLAG, DISPATCH_CMD_ENV, ShellDispatcher, build_runner, is_armed)
from zo_sentinel.sft.ingest import IngestQueue  # noqa: E402
from zo_sentinel.sft.schema import (  # noqa: E402
    DatasetRef, DispatchSpec, JobSpec, JobStatus, StudentSpec)


# --- fixtures -----------------------------------------------------------------

def _corpus_row(i: int, verdict: str = "TRUSTED_RESEARCH") -> dict:
    return {
        "messages": [
            {"role": "system", "content": "You are a security analyst."},
            {"role": "user", "content": f"EVIDENCE for server {i}"},
            {"role": "assistant", "content": json.dumps({
                "verdict": verdict, "confidence": 0.8,
                "evidence_citations": ["EVIDENCE"]})},
        ],
        "metadata": {"server_id": f"srv{i:05d}", "label_source": "teacher"},
    }


def _servers(batch: Batch) -> list[dict]:
    return [{"server_id": f"srv{i:05d}", "name": f"s{i}", "url": "https://x"}
            for i in range(batch.start, batch.end)]


def _good_teacher(servers: list[dict]) -> list[dict]:
    return [_corpus_row(i) for i in range(len(servers))]


# --- gate: the two poles -------------------------------------------------------

def test_gate_red_on_degenerate_scores():
    """The 70-prior collapse: everyone scores ~70 -> RED on spread AND clustering."""
    res = evaluate_scores([70.0] * 50)
    assert not res.green
    assert res.checks["spread"] is False
    assert res.checks["anti_clustering"] is False
    assert any("not discriminating" in r for r in res.reasons)


def test_gate_red_on_clustered_scores():
    """Spread exists but 90% share one bucket (the anomaly_detector MEDIUM pole)."""
    scores = [70.0] * 45 + [10.0, 20.0, 30.0, 90.0, 95.0]
    res = evaluate_scores(scores)
    assert not res.green
    assert res.checks["anti_clustering"] is False


def test_gate_green_on_discriminating_scores():
    scores = [float(s) for s in range(5, 100, 2)]  # spread, no cluster
    trusted = [85.0, 90.0, 92.0, 88.0, 95.0]
    threat = [12.0, 20.0, 15.0, 30.0, 25.0]
    res = evaluate_scores(scores, trusted_scores=trusted, threat_scores=threat,
                          null_slice_abstained=19, null_slice_total=20,
                          total_abstained=10)
    assert res.green, res.reasons
    assert res.checks["spread"] and res.checks["separation"]


def test_gate_red_on_inverted_separation():
    """Spread is fine but threats score HIGH -- ordering is wrong -> RED."""
    scores = [float(s) for s in range(5, 100, 2)]
    res = evaluate_scores(scores, trusted_scores=[20.0, 25.0, 30.0],
                          threat_scores=[80.0, 85.0, 90.0])
    assert not res.green
    assert res.checks["separation"] is False


def test_gate_red_on_tiny_sample():
    res = evaluate_scores([10.0, 90.0])
    assert not res.green
    assert res.checks["sample"] is False


# --- trigger ---------------------------------------------------------------------

def test_trigger_batches_and_ordered_resolution(tmp_path):
    t = RowTrigger(tmp_path / "state.json")
    assert t.pending_batches(999) == []           # partial tail is not a batch
    batches = t.pending_batches(2500)
    assert [(b.start, b.end) for b in batches] == [(0, 1000), (1000, 2000)]
    # deterministic ids
    assert batches[0].batch_id == Batch(0, 1000).batch_id
    t.resolve(batches[0], outcome="appended")
    assert t.watermark == 1000
    # reload from disk -- durable
    t2 = RowTrigger(tmp_path / "state.json")
    assert t2.watermark == 1000
    assert [(b.start, b.end) for b in t2.pending_batches(2500)] == [(1000, 2000)]
    # out-of-order resolution is a programming error, not a quiet skip
    with pytest.raises(ValueError):
        t2.resolve(Batch(2000, 3000), outcome="appended")


def test_trigger_refuses_corrupt_state(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to guess"):
        RowTrigger(p)


# --- corpus: the 2026-04-29 pole ---------------------------------------------------

def test_corpus_quarantines_empty_teacher_return(tmp_path):
    c = CorpusStore(tmp_path / "corpus")
    res = c.append_batch("deadbeef00000001", [], expected_count=1000)
    assert not res.accepted
    assert "2026-04-29" in " ".join(res.errors)
    assert not c.segment_path("deadbeef00000001").exists()
    assert c.quarantine_path("deadbeef00000001").exists()
    assert c.total_rows() == 0
    # the rejection is durable in the manifest -- no silent drop
    assert any(e["decision"] == "quarantined" for e in c.manifest_entries())


def test_corpus_quarantines_undersized_return(tmp_path):
    c = CorpusStore(tmp_path / "corpus")
    rows = [_corpus_row(i) for i in range(100)]   # 100 of 1000 < min_fraction 0.5
    res = c.append_batch("deadbeef00000002", rows, expected_count=1000)
    assert not res.accepted
    assert c.total_rows() == 0


def test_corpus_rejects_dialect_drift(tmp_path):
    """A verdict outside the canonical 7-term set (design §1.4) is a validation
    error -- the third dialect cannot leak into the corpus."""
    c = CorpusStore(tmp_path / "corpus")
    rows = [_corpus_row(0, verdict="REVIEW")]     # scorer dialect, not canonical
    res = c.append_batch("deadbeef00000003", rows, expected_count=1)
    assert not res.accepted
    assert any("canonical" in e for e in res.errors)


def test_corpus_appends_and_replay_is_noop(tmp_path):
    c = CorpusStore(tmp_path / "corpus")
    rows = [_corpus_row(i) for i in range(900)]
    res = c.append_batch("deadbeef00000004", rows, expected_count=1000,
                         server_ids=[f"srv{i}" for i in range(900)])
    assert res.accepted and res.rows == 900
    assert c.total_rows() == 900
    sha_before = c.manifest_sha()
    replay = c.append_batch("deadbeef00000004", rows, expected_count=1000)
    assert replay.accepted and replay.duplicate
    assert c.total_rows() == 900                  # unchanged
    assert c.manifest_sha() == sha_before          # same corpus state -> same key


# --- loop: dry-run vs live, and idempotence ------------------------------------------

def _tick(tmp_path, *, dry_run, teacher=_good_teacher, count=2500, threshold=1500):
    return run_once(
        registry_count=count, fetch_fn=_servers, teacher_fn=teacher,
        state_path=tmp_path / "state.json", corpus_dir=tmp_path / "corpus",
        queue_dir=tmp_path / "queue", dry_run=dry_run,
        retrain_threshold=threshold)


def test_loop_dry_run_enqueues_nothing(tmp_path):
    report = _tick(tmp_path, dry_run=True)
    assert report.batches_seen == 2
    assert report.enqueued is None
    # nothing moved, nothing written
    assert RowTrigger(tmp_path / "state.json").watermark == 0
    assert CorpusStore(tmp_path / "corpus").total_rows() == 0
    assert IngestQueue(tmp_path / "queue").list() == []
    # ...but the rehearsal SAYS what it would do
    assert any("WOULD append" in n for n in report.notes)


def test_loop_live_tick_appends_and_enqueues_once(tmp_path):
    report = _tick(tmp_path, dry_run=False)
    assert report.batches_appended == 2
    assert RowTrigger(tmp_path / "state.json").watermark == 2000
    assert CorpusStore(tmp_path / "corpus").total_rows() == 2000
    assert report.enqueued is not None
    q = IngestQueue(tmp_path / "queue")
    assert len(q.list(JobStatus.QUEUED)) == 1
    # second tick: same corpus state -> same job_id -> no duplicate enqueue
    report2 = _tick(tmp_path, dry_run=False)
    assert report2.batches_seen == 0
    assert report2.enqueued is None
    assert len(q.list(JobStatus.QUEUED)) == 1
    assert any("idempotent" in n for n in report2.notes)


def test_loop_dry_run_would_enqueue_when_corpus_ready(tmp_path):
    """Grow the corpus live, then rehearse: dry-run names the job it WOULD
    submit and the queue stays empty of it."""
    _tick(tmp_path, dry_run=False, count=1000, threshold=99999)  # append, no retrain
    report = _tick(tmp_path, dry_run=True, count=1000, threshold=500)
    assert report.would_enqueue is not None
    assert IngestQueue(tmp_path / "queue").get(report.would_enqueue) is None
    assert any("NOT enqueuing" in n for n in report.notes)


def test_loop_quarantine_then_fail_out(tmp_path):
    """An always-empty teacher (the 2026-04-29 pathology) retries MAX_ATTEMPTS
    times, then fails the batch out LOUDLY: manifest row + mesh_event, and the
    watermark moves on instead of wedging the loop forever."""
    events = []
    empty_teacher = lambda servers: []  # noqa: E731
    for _ in range(3):
        run_once(registry_count=1000, fetch_fn=_servers, teacher_fn=empty_teacher,
                 state_path=tmp_path / "state.json", corpus_dir=tmp_path / "corpus",
                 queue_dir=tmp_path / "queue", dry_run=False,
                 emit_fn=lambda t, p: events.append((t, p)))
    c = CorpusStore(tmp_path / "corpus")
    assert c.total_rows() == 0
    assert any(e["decision"] == "failed_out" for e in c.manifest_entries())
    assert RowTrigger(tmp_path / "state.json").watermark == 1000
    assert events and events[0][0] == "label_loop_batch_failed"


# --- the single flag --------------------------------------------------------------

def test_flag_closed_runner_is_dormant(tmp_path, monkeypatch):
    monkeypatch.delenv(ARM_FLAG, raising=False)
    monkeypatch.delenv("SFT_BATCH_ENABLED", raising=False)
    runner = build_runner(IngestQueue(tmp_path / "q"))
    assert not is_armed()
    assert runner.dispatcher.name == "noop"
    assert not runner.is_enabled()
    assert runner.run_once() is None              # dormant: claims nothing


def test_flag_open_selects_real_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setenv(ARM_FLAG, "1")
    runner = build_runner(IngestQueue(tmp_path / "q"))
    assert is_armed()
    assert isinstance(runner.dispatcher, ShellDispatcher)
    assert runner.is_enabled()
    assert runner.activation_reason() == "constructor enabled=True"


def test_shell_dispatcher_refuses_unarmed():
    d = ShellDispatcher(env={})                   # flag closed in this env
    job = JobSpec(job_id="sft_feedfeedfeed", method="sft",
                  student=StudentSpec(base_model="qwen2.5-3b"),
                  dataset=DatasetRef(train_path="x.jsonl"),
                  resources={"accelerators": {"RTXA5000": 1}},
                  dispatch=DispatchSpec(backend="vast", dry_run=False))
    out = d.dispatch(job)
    assert not out.ok and not out.launched
    assert "HELD" in out.detail


def test_shell_dispatcher_honours_job_dry_run():
    """Even armed, a dry_run job records intent and launches nothing."""
    d = ShellDispatcher(env={ARM_FLAG: "1", DISPATCH_CMD_ENV: "/no/such/cmd"})
    job = JobSpec(job_id="sft_feedfeedfeee", method="sft",
                  student=StudentSpec(base_model="qwen2.5-3b"),
                  dataset=DatasetRef(train_path="x.jsonl"),
                  resources={"accelerators": {"RTXA5000": 1}},
                  dispatch=DispatchSpec(backend="vast", dry_run=True))
    out = d.dispatch(job)
    assert out.ok and not out.launched
    assert "DRY-RUN" in out.detail


def test_shell_dispatcher_refuses_without_command():
    d = ShellDispatcher(env={ARM_FLAG: "1"})      # armed but no command configured
    job = JobSpec(job_id="sft_feedfeedfeef", method="sft",
                  student=StudentSpec(base_model="qwen2.5-3b"),
                  dataset=DatasetRef(train_path="x.jsonl"),
                  resources={"accelerators": {"RTXA5000": 1}},
                  dispatch=DispatchSpec(backend="vast", dry_run=False))
    out = d.dispatch(job)
    assert not out.ok and not out.launched
    assert DISPATCH_CMD_ENV in out.detail
