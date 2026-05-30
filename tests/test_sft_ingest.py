"""Hermetic tests for the SFT ingestion component (zo_sentinel.sft).

No torch, no cloud, no GPU -- pure filesystem + JSON. Mirrors the contract the
zomesh-sentinel-sft pipeline consumes (messages-format SFT rows, preference
DPO rows) and proves the runner stays DORMANT until explicitly activated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.sft.schema import (  # noqa: E402
    DatasetRef,
    DispatchSpec,
    JobSpec,
    JobStatus,
    StudentSpec,
    validate_dataset_file,
    validate_job,
)
from zo_sentinel.sft.ingest import IngestQueue, compute_file_digest, new_job_id  # noqa: E402
from zo_sentinel.sft.batch_runner import BatchRunner, NoopDispatcher, queue_status_report  # noqa: E402


# --- fixtures ---------------------------------------------------------------

def _messages_rows(n: int) -> str:
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "messages": [
                {"role": "system", "content": "You are a security analyst."},
                {"role": "user", "content": f"Label server {i}"},
                {"role": "assistant", "content": "TRUSTED"},
            ],
            "metadata": {"server_id": f"srv{i:04d}"},
        }))
    return "\n".join(lines) + "\n"


def _good_spec(train_path: str, fmt: str = "messages") -> JobSpec:
    return JobSpec(
        job_id=new_job_id(),
        method="sft",
        student=StudentSpec(base_model="qwen2.5-3b", init_adapter="student_v1",
                            output_name="student_v2"),
        dataset=DatasetRef(train_path=train_path, fmt=fmt),
        resources={"accelerators": {"RTXA5000": 1}, "cloud": "runpod"},
        dispatch=DispatchSpec(backend="noop", dry_run=True, recipe="sft_v3_dpo.yaml"),
    )


@pytest.fixture
def train_file(tmp_path: Path) -> Path:
    p = tmp_path / "train.jsonl"
    p.write_text(_messages_rows(5), encoding="utf-8")
    return p


# --- schema (de)serialization ----------------------------------------------

class TestSchema:
    def test_roundtrip(self, train_file: Path):
        spec = _good_spec(str(train_file))
        again = JobSpec.from_json(spec.to_json())
        assert again.job_id == spec.job_id
        assert again.method == "sft"
        assert again.student.base_model == "qwen2.5-3b"
        assert again.dataset.fmt == "messages"
        assert again.dispatch.backend == "noop"
        assert again.status == JobStatus.RECEIVED

    def test_mark_records_history(self, train_file: Path):
        spec = _good_spec(str(train_file))
        spec.mark(JobStatus.QUEUED, "ready")
        assert spec.status == JobStatus.QUEUED
        assert spec.history[-1]["status"] == "queued"
        assert spec.history[-1]["note"] == "ready"


# --- validation -------------------------------------------------------------

class TestValidation:
    def test_good_job_passes(self, train_file: Path):
        res = validate_job(_good_spec(str(train_file)))
        assert res.ok, res.errors

    def test_bad_method_rejected(self, train_file: Path):
        spec = _good_spec(str(train_file))
        spec.method = "telepathy"
        res = validate_job(spec)
        assert not res.ok
        assert any("method" in e for e in res.errors)

    def test_missing_accelerators_rejected(self, train_file: Path):
        spec = _good_spec(str(train_file))
        spec.resources = {"cloud": "runpod"}
        res = validate_job(spec)
        assert not res.ok
        assert any("accelerators" in e for e in res.errors)

    def test_bad_job_id_rejected(self, train_file: Path):
        spec = _good_spec(str(train_file))
        spec.job_id = "not-a-valid-id"
        res = validate_job(spec)
        assert not res.ok

    def test_remote_dataset_defers_with_warning(self):
        spec = _good_spec("hf://org/dataset@main")
        res = validate_job(spec)
        assert res.ok  # structurally fine
        assert any("deferred" in w for w in res.warnings)

    def test_messages_format_validation(self, tmp_path: Path):
        good = tmp_path / "g.jsonl"
        good.write_text(_messages_rows(3), encoding="utf-8")
        assert validate_dataset_file(good, "messages").ok

    def test_messages_missing_key_fails(self, tmp_path: Path):
        bad = tmp_path / "b.jsonl"
        bad.write_text(json.dumps({"metadata": {}}) + "\n", encoding="utf-8")
        res = validate_dataset_file(bad, "messages")
        assert not res.ok

    def test_preference_format_validation(self, tmp_path: Path):
        p = tmp_path / "pref.jsonl"
        p.write_text(json.dumps({"chosen": "A", "rejected": "B"}) + "\n", encoding="utf-8")
        assert validate_dataset_file(p, "preference").ok

    def test_invalid_json_line_fails(self, tmp_path: Path):
        p = tmp_path / "broken.jsonl"
        p.write_text('{"messages": [}}\n', encoding="utf-8")
        res = validate_dataset_file(p, "messages")
        assert not res.ok

    def test_empty_file_fails(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert not validate_dataset_file(p, "messages").ok


# --- ingest queue -----------------------------------------------------------

class TestIngestQueue:
    def test_digest_is_deterministic(self, train_file: Path):
        rows, sha = compute_file_digest(train_file)
        rows2, sha2 = compute_file_digest(train_file)
        assert rows == 5 and rows2 == 5
        assert sha == sha2 and len(sha) == 64

    def test_submit_good_job_queued_with_digest(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        job = q.submit(_good_spec(str(train_file)))
        assert job.status == JobStatus.QUEUED
        assert job.dataset.verified is True
        assert job.dataset.rows == 5
        assert len(job.dataset.sha256) == 64
        assert q.counts()["queued"] == 1

    def test_submit_bad_job_rejected(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        spec = _good_spec(str(train_file))
        spec.method = "nope"
        job = q.submit(spec)
        assert job.status == JobStatus.REJECTED
        assert q.counts()["rejected"] == 1
        assert q.counts()["queued"] == 0
        # rejection reasons are auditable in history
        assert any("method" in h["note"] for h in job.history)

    def test_get_and_list(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        job = q.submit(_good_spec(str(train_file)))
        assert q.get(job.job_id).job_id == job.job_id
        assert [j.job_id for j in q.list(JobStatus.QUEUED)] == [job.job_id]

    def test_transition_moves_file(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        job = q.submit(_good_spec(str(train_file)))
        q.transition(job.job_id, JobStatus.DONE, "finished")
        assert q.counts()["queued"] == 0
        assert q.counts()["done"] == 1
        assert q.get(job.job_id).status == JobStatus.DONE


# --- batch runner: DORMANCY is the headline contract ------------------------

class TestBatchRunnerDormant:
    def test_dormant_by_default_claims_nothing(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        q.submit(_good_spec(str(train_file)))
        runner = BatchRunner(q)
        assert runner.is_enabled() is False
        assert runner.claim_next() is None
        assert runner.run_once() is None
        assert runner.drain() == []
        # job stays QUEUED, untouched
        assert q.counts()["queued"] == 1

    def test_env_does_not_leak_enable(self, tmp_path: Path, monkeypatch):
        # ensure a stray env var can't silently enable in other tests
        monkeypatch.delenv("SFT_BATCH_ENABLED", raising=False)
        q = IngestQueue(tmp_path / "q")
        assert BatchRunner(q).is_enabled() is False


class TestBatchRunnerActivated:
    def test_enabled_via_constructor_claims(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        q.submit(_good_spec(str(train_file)))
        runner = BatchRunner(q, enabled=True)
        claimed = runner.claim_next()
        assert claimed is not None
        assert claimed.status == JobStatus.CLAIMED

    def test_enabled_via_env(self, tmp_path: Path, train_file: Path, monkeypatch):
        monkeypatch.setenv("SFT_BATCH_ENABLED", "1")
        q = IngestQueue(tmp_path / "q")
        q.submit(_good_spec(str(train_file)))
        assert BatchRunner(q).is_enabled() is True

    def test_enabled_via_sentinel(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        q.submit(_good_spec(str(train_file)))
        (q.root / ".batch_enabled").write_text("", encoding="utf-8")
        assert BatchRunner(q).is_enabled() is True

    def test_noop_dispatch_records_but_never_launches(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        q.submit(_good_spec(str(train_file)))
        runner = BatchRunner(q, enabled=True, dispatcher=NoopDispatcher())
        outcome = runner.run_once()
        assert outcome is not None
        assert outcome.ok is True
        assert outcome.launched is False           # <-- never starts a GPU job
        assert "DRY-RUN" in outcome.detail
        # dry-run leaves it CLAIMED, not RUNNING
        assert q.get(q.list(JobStatus.CLAIMED)[0].job_id).status == JobStatus.CLAIMED

    def test_priority_order(self, tmp_path: Path, train_file: Path):
        q = IngestQueue(tmp_path / "q")
        lo = _good_spec(str(train_file)); lo.priority = 10
        hi = _good_spec(str(train_file)); hi.priority = 200
        q.submit(hi)
        q.submit(lo)
        runner = BatchRunner(q, enabled=True)
        claimed = runner.claim_next()
        assert claimed.job_id == lo.job_id          # lower priority value first


# --- status report ----------------------------------------------------------

def test_queue_status_report_shows_dormant(tmp_path: Path, train_file: Path, monkeypatch):
    monkeypatch.delenv("SFT_BATCH_ENABLED", raising=False)
    q = IngestQueue(tmp_path / "q")
    q.submit(_good_spec(str(train_file)))
    rep = queue_status_report(tmp_path / "q")
    assert rep["enabled"] is False
    assert rep["counts"]["queued"] == 1
    assert rep["dispatcher"] == "noop"
    assert "dormant" in rep["activation"]
