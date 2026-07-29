"""Tests for tools/shadow_decision.py -- the C4 shadow-agreement ledger.

WHY THESE EXIST (FU-177, 2026-07-29)
------------------------------------
This module is the instrument that will be cited when Phase 2 autonomy is
granted. Until this file existed it had ZERO tests and lived only as an
uncommitted tower-local script, while its three siblings (fire_gate,
accept_gate, sentinel_run_ledger) all sit in the repo under CI.

The specific hole: nothing checked WHEN a decision was recorded. The chairman
fired 7fc39201 at 17:26:10Z; the shadow decision for it was written at
17:31:05Z and reconciled as an AGREEMENT, and `--status` duly reported
1 agreement / 1 consecutive toward the 10/8 bar. A decision recorded after the
outcome is observable cannot disagree, so it is not evidence about the
autonomous decider -- and it moved the counter toward granting authority.

NEGATIVE CONTROL (R4) -- every assertion below was seen RED before it was
trusted. Two SURGICAL mutants of the shipped file, each of which COMPILES
(ast.parse asserted before running -- a SyntaxError is not a red control):

  mutant 1  `_counted()` -> `return rec.get("post_hoc") is not True`
            i.e. treat UNKNOWN as countable. This is the shape of the real bug
            for LEGACY records: the 2026-07-29 agreement has no post_hoc key at
            all, so under this mutant it counts again.

  mutant 2  in `status()`, `counted = done`
            i.e. drop the temporal exclusion entirely -- the pre-guard
            behaviour, which is precisely what reported the post-hoc agreement
            as real.

A blunt mutant (e.g. `return False` in `_counted`) was rejected on purpose: it
makes NOTHING countable and so also breaks the happy-path tests, which would
have made the suite look like it covered this when it only noticed vandalism.
"""

import argparse
import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "tools", "shadow_decision.py")

_spec = importlib.util.spec_from_file_location("shadow_decision", _TOOL)
shadow_decision = importlib.util.module_from_spec(_spec)
sys.modules["shadow_decision"] = shadow_decision
_spec.loader.exec_module(shadow_decision)

_REAL_LIVE_PROD_SHA = shadow_decision._live_prod_sha

SHA_CAND = "ae71dafd1295ce03463a520f38678edb8a78a3a3"
SHA_LIVE = "7fc39201d8aea5f50017bf893843694e5a77f7f1"
SHA_OTHER = "0ada3c0c7cfabc5ca17f985528c17697cb5d8013"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Never touch the real ledger, and never reach the real network."""
    monkeypatch.setattr(
        shadow_decision, "STORE", str(tmp_path / "shadow_decisions.jsonl")
    )
    monkeypatch.setattr(
        shadow_decision,
        "_live_prod_sha",
        lambda: pytest.fail("test did not stub the prod probe"),
    )
    yield


def _prod_serving(monkeypatch, sha, err=""):
    monkeypatch.setattr(shadow_decision, "_live_prod_sha", lambda: (sha, err))


def _probe_broken(monkeypatch, err="URLError: unreachable"):
    monkeypatch.setattr(shadow_decision, "_live_prod_sha", lambda: (None, err))


def _rec_args(sha=SHA_CAND, would_fire="yes", klass="A", acted="no", reasons="r"):
    return argparse.Namespace(
        sha=sha, klass=klass, would_fire=would_fire, migrations_tree="30dcd9fa",
        reasons=reasons, acted=acted,
    )


def _rec_args_full(**kw):
    base = dict(sha=SHA_CAND, klass="A", would_fire="yes",
                migrations_tree="30dcd9fa", reasons="r", acted="no")
    base.update(kw)
    return argparse.Namespace(**base)


def _reconcile_args(fired_sha=None, held_sha=None, fired_at=None):
    return argparse.Namespace(fired_sha=fired_sha, held_sha=held_sha,
                             fired_at=fired_at)


def _amend_args(sha=SHA_LIVE, post_hoc="yes", evidence="because", fired_at=None):
    return argparse.Namespace(sha=sha, post_hoc=post_hoc, evidence=evidence,
                             fired_at=fired_at)


def _status(json_out=True, gate=False):
    return argparse.Namespace(json=json_out, gate=gate)


def _read_store():
    return shadow_decision._load()


def _status_dict(capsys):
    capsys.readouterr()          # drain whatever record/reconcile printed first
    assert shadow_decision.status(_status()) == 0
    return json.loads(capsys.readouterr().out)


def _write_raw(rec):
    shadow_decision._append(rec)


# --------------------------------------------------------------------------
# the temporal guard itself
# --------------------------------------------------------------------------

def test_record_marks_prediction_when_prod_serves_a_different_sha(monkeypatch):
    """The legitimate case: prod is on X, we predict about Y."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(sha=SHA_CAND)) == 0
    (rec,) = _read_store()
    assert rec["post_hoc"] is False
    assert rec["prod_sha_at_record"] == SHA_LIVE


def test_record_marks_post_hoc_when_prod_already_serves_the_sha(monkeypatch):
    """THE HOLE: a decision about a sha prod is ALREADY running is not a prediction."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(sha=SHA_LIVE)) == 0
    (rec,) = _read_store()
    assert rec["post_hoc"] is True


def test_post_hoc_agreement_is_not_counted_toward_c4(monkeypatch, capsys):
    """The 2026-07-29 event, replayed: agreement recorded after the fire.

    It must appear in reconciled_decisions (we do not hide it) but contribute
    ZERO agreements and ZERO consecutive agreements.
    """
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_LIVE, would_fire="yes"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_LIVE)) == 0

    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 1
    assert out["counted_decisions"] == 0
    assert out["excluded_post_hoc"] == 1
    assert out["agreements"] == 0
    assert out["consecutive_agreements"] == 0
    assert out["C4_met"] is False


def test_real_prediction_is_counted_toward_c4(monkeypatch, capsys):
    """The guard must not be a blanket refusal -- a genuine prediction counts."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND, would_fire="yes"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 0

    out = _status_dict(capsys)
    assert out["counted_decisions"] == 1
    assert out["agreements"] == 1
    assert out["consecutive_agreements"] == 1
    assert out["excluded_post_hoc"] == 0


# --------------------------------------------------------------------------
# unknown is not zero (R6)
# --------------------------------------------------------------------------

def test_probe_failure_records_unknown_not_prediction(monkeypatch):
    _probe_broken(monkeypatch)
    assert shadow_decision.record(_rec_args_full(sha=SHA_CAND)) == 0
    (rec,) = _read_store()
    assert rec["post_hoc"] == shadow_decision.UNKNOWN
    assert rec["post_hoc"] is not False
    assert "unreachable" in rec["prod_sha_probe_error"]


def test_unknown_probe_agreement_is_not_counted(monkeypatch, capsys):
    _probe_broken(monkeypatch)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND))
    out = _status_dict(capsys)
    assert out["counted_decisions"] == 0
    assert out["excluded_post_hoc_unknown"] == 1
    assert out["agreements"] == 0


def test_prod_serving_literal_unknown_is_not_a_resolvable_sha(monkeypatch):
    """v64 served git_sha "unknown" for four days.

    Reading that as "prod is on something else" would hand out credit, so the
    real probe must report it as UNEVALUATED. This exercises the shipped
    _live_prod_sha body, not a stub.
    """
    class _Resp:
        status = 200

        def read(self):
            return json.dumps({"git_sha": "unknown"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    # the autouse fixture stubs the probe out; this test is ABOUT the real body
    monkeypatch.setattr(shadow_decision, "_live_prod_sha", _REAL_LIVE_PROD_SHA)
    sha, err = shadow_decision._live_prod_sha()
    assert sha is None
    assert "not a resolvable sha" in err


def test_legacy_record_without_post_hoc_key_is_not_counted(capsys):
    """The record that actually exists in the live ledger has no post_hoc key.

    It must stop counting the moment this guard ships -- silently regrading it
    to False would be the same laundering in reverse.
    """
    _write_raw({
        "ts_utc": "2026-07-29T17:31:05Z", "sha": SHA_LIVE, "hazard_class": "A",
        "would_fire": True, "outcome": "agreed", "human_fired": True,
        "reconciled_utc": "2026-07-29T17:38:06Z",
        "task_would_fire_human_held": False, "restages": 1,
    })
    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 1
    assert out["counted_decisions"] == 0
    assert out["excluded_post_hoc_unknown"] == 1
    assert out["agreements"] == 0
    assert out["C4_met"] is False


# --------------------------------------------------------------------------
# network-free temporal fallback (R7: recover the judgement, don't refuse it)
# --------------------------------------------------------------------------

def test_reconcile_fired_at_before_decision_flips_to_post_hoc(monkeypatch, capsys):
    _probe_broken(monkeypatch)                       # probe could not evaluate
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    (rec,) = _read_store()
    rec_ts = rec["ts_utc"]
    assert rec["post_hoc"] == shadow_decision.UNKNOWN

    # Human action strictly BEFORE the decision was written.
    shadow_decision.reconcile(
        _reconcile_args(fired_sha=SHA_CAND, fired_at="2000-01-01T00:00:00Z")
    )
    (rec,) = _read_store()
    assert rec["post_hoc"] is True
    assert rec_ts in rec["post_hoc_reason"]
    out = _status_dict(capsys)
    assert out["excluded_post_hoc"] == 1
    assert out["agreements"] == 0


def test_reconcile_fired_at_after_decision_preserves_the_prediction(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(
        _reconcile_args(fired_sha=SHA_CAND, fired_at="2099-01-01T00:00:00Z")
    )
    (rec,) = _read_store()
    assert rec["post_hoc"] is False
    assert rec["human_action_at"] == "2099-01-01T00:00:00Z"
    assert _status_dict(capsys)["agreements"] == 1


def test_unparseable_fired_at_does_not_silently_pass_or_crash(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    assert shadow_decision.reconcile(
        _reconcile_args(fired_sha=SHA_CAND, fired_at="not-a-timestamp")
    ) == 0
    (rec,) = _read_store()
    assert rec["post_hoc"] is False            # probe verdict stands, unchanged
    assert "human_action_at" not in rec        # and no bogus instant recorded


def test_restage_after_the_sha_goes_live_becomes_post_hoc(monkeypatch):
    """Staged, then fired, then restaged: the restage must not launder it."""
    _prod_serving(monkeypatch, SHA_OTHER)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    (rec,) = _read_store()
    assert rec["post_hoc"] is False

    _prod_serving(monkeypatch, SHA_CAND)                # it went live
    assert shadow_decision.record(_rec_args_full(sha=SHA_CAND, reasons="again")) == 0
    (rec,) = _read_store()
    assert rec["post_hoc"] is True
    assert rec["restages"] == 2


# --------------------------------------------------------------------------
# consecutive run and C4_met arithmetic over COUNTED decisions only
# --------------------------------------------------------------------------

def test_consecutive_run_ignores_excluded_decisions(monkeypatch, capsys):
    """A post-hoc record in the middle must neither add to nor break the run."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND))

    shadow_decision.record(_rec_args_full(sha=SHA_LIVE))          # post-hoc
    shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_LIVE))

    _prod_serving(monkeypatch, SHA_CAND)
    shadow_decision.record(_rec_args_full(sha=SHA_OTHER))
    shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_OTHER))

    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 3
    assert out["counted_decisions"] == 2
    assert out["consecutive_agreements"] == 2


def test_c4_met_cannot_be_reached_on_post_hoc_records(monkeypatch, capsys):
    """Enough post-hoc agreements to clear 10/8 must still leave C4 unmet."""
    monkeypatch.setattr(shadow_decision, "REQUIRED_TOTAL", 3)
    monkeypatch.setattr(shadow_decision, "REQUIRED_CONSECUTIVE", 3)
    for i in range(4):
        sha = "%040d" % i
        _prod_serving(monkeypatch, sha)                  # prod already on it
        shadow_decision.record(_rec_args_full(sha=sha))
        shadow_decision.reconcile(_reconcile_args(fired_sha=sha))
    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 4
    assert out["counted_decisions"] == 0
    assert out["C4_met"] is False
    assert shadow_decision.status(_status(json_out=False, gate=True)) == 1


def test_gate_passes_only_on_counted_predictions(monkeypatch, capsys):
    monkeypatch.setattr(shadow_decision, "REQUIRED_TOTAL", 2)
    monkeypatch.setattr(shadow_decision, "REQUIRED_CONSECUTIVE", 2)
    for i in range(2):
        sha = "%040d" % i
        _prod_serving(monkeypatch, SHA_LIVE)             # prod on something else
        shadow_decision.record(_rec_args_full(sha=sha))
        shadow_decision.reconcile(_reconcile_args(fired_sha=sha))
    assert shadow_decision.status(_status(json_out=False, gate=True)) == 0
    capsys.readouterr()


def test_unsafe_disagreement_blocks_c4(monkeypatch, capsys):
    """Task said FIRE, human HELD -- the only direction that matters."""
    monkeypatch.setattr(shadow_decision, "REQUIRED_TOTAL", 1)
    monkeypatch.setattr(shadow_decision, "REQUIRED_CONSECUTIVE", 1)
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND, would_fire="yes"))
    assert shadow_decision.reconcile(_reconcile_args(held_sha=SHA_CAND)) == 1
    out = _status_dict(capsys)
    assert out["disagreements_task_would_fire_human_held"] == 1
    assert out["C4_met"] is False


# --------------------------------------------------------------------------
# amendment: an explicit, evidenced regrade -- never a silent one
# --------------------------------------------------------------------------

def test_amend_requires_evidence():
    _write_raw({"sha": SHA_LIVE, "outcome": "agreed", "would_fire": True})
    assert shadow_decision.amend(_amend_args(evidence=None)) == 2
    (rec,) = _read_store()
    assert "post_hoc" not in rec              # refused amendment changed nothing


def test_amend_rejects_a_bogus_verdict():
    _write_raw({"sha": SHA_LIVE, "outcome": "agreed", "would_fire": True})
    assert shadow_decision.amend(_amend_args(post_hoc="maybe")) == 2


def test_amend_decounts_the_live_post_hoc_agreement(capsys):
    """Exactly the regrade this run performs on the real ledger."""
    _write_raw({
        "ts_utc": "2026-07-29T17:31:05Z", "sha": SHA_LIVE, "hazard_class": "A",
        "would_fire": True, "outcome": "agreed", "human_fired": True,
        "post_hoc": False,                     # pretend it had been counted
        "task_would_fire_human_held": False, "restages": 1,
    })
    assert _status_dict(capsys)["agreements"] == 1
    assert shadow_decision.amend(
        _amend_args(post_hoc="yes", evidence="fired 17:26:10Z, recorded 17:31:05Z",
                    fired_at="2026-07-29T17:26:10Z")
    ) == 0
    out = _status_dict(capsys)
    assert out["agreements"] == 0
    assert out["excluded_post_hoc"] == 1
    (rec,) = _read_store()
    assert rec["human_action_at"] == "2026-07-29T17:26:10Z"
    assert "17:26:10Z" in rec["post_hoc_reason"]
    assert rec["amended_utc"]


def test_amend_on_missing_sha_is_unknown_not_success():
    assert shadow_decision.amend(_amend_args()) == 2


# --------------------------------------------------------------------------
# behaviours that predate this change and must not regress
# --------------------------------------------------------------------------

def test_acted_yes_is_refused_and_writes_nothing():
    assert shadow_decision.record(_rec_args_full(acted="yes")) == 2
    assert _read_store() == []


def test_bad_sha_and_bad_class_are_refused():
    assert shadow_decision.record(_rec_args_full(sha="short")) == 2
    assert shadow_decision.record(_rec_args_full(klass="C")) == 2
    assert _read_store() == []


def test_restage_does_not_inflate_the_denominator(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    for _ in range(5):
        shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    recs = _read_store()
    assert len(recs) == 1
    assert recs[0]["restages"] == 5
    assert _status_dict(capsys)["pending_not_counted"] == 1


def test_reconcile_without_a_prior_decision_is_unknown_not_agreement():
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 2


def test_pending_decisions_are_never_agreements(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    out = _status_dict(capsys)
    assert out["pending_not_counted"] == 1
    assert out["reconciled_decisions"] == 0
    assert out["agreements"] == 0


def test_corrupt_line_does_not_erase_history(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    with open(shadow_decision.STORE, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert len(_read_store()) == 1


def test_no_store_yet_reads_as_unproven_not_met(capsys):
    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 0
    assert out["C4_met"] is False
