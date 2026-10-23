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


def _reconcile_args(fired_sha=None, held_sha=None, fired_at=None,
                    superseded_sha=None, superseded_by=None, evidence=None):
    return argparse.Namespace(fired_sha=fired_sha, held_sha=held_sha,
                              superseded_sha=superseded_sha,
                              superseded_by=superseded_by, evidence=evidence,
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
    assert shadow_decision.reconcile(_reconcile_args(
        held_sha=SHA_CAND,
        evidence="chairman replied on 2026-07-29 declining to fire")) == 1
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


# --------------------------------------------------------------------------
# FU-180: SUPERSEDE IS NOT A DECLINE
#
# `task_would_fire_human_held` is a claim about the DECIDER. "Main advanced past
# the staged candidate" is evidence about nobody, and the sentinel merges a PR on
# most runs -- so scoring a supersede as a hold would let its own housekeeping
# permanently reset the counter that is supposed to measure judgement.
#
# NEGATIVE CONTROLS for this block (R4), each a surgical mutant that COMPILES:
#   mutant 3  in reconcile(), delete the `if superseded:` branch so a supersede
#             falls through to the hold path (human_fired False -> "disagreed",
#             task_would_fire_human_held True). This is EXACTLY the pre-fix
#             behaviour the charter instructed, so it is the shape of the real
#             bug rather than vandalism.
#   mutant 4  in status(), `done = [... in ("agreed", "disagreed", "superseded")]`
#             i.e. count supersedes in the denominator.
# --------------------------------------------------------------------------

def _agree(monkeypatch, sha):
    """One genuine, countable agreement on `sha`."""
    _prod_serving(monkeypatch, SHA_LIVE)                 # prod is elsewhere
    shadow_decision.record(_rec_args_full(sha=sha))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=sha)) == 0


def test_supersede_returns_zero_not_a_disagreement(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    assert shadow_decision.reconcile(
        _reconcile_args(superseded_sha=SHA_CAND)) == 0
    assert "SUPERSEDED" in capsys.readouterr().out


def test_supersede_is_in_neither_numerator_nor_denominator(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(superseded_sha=SHA_CAND))
    out = _status_dict(capsys)
    assert out["superseded_not_counted"] == 1
    assert out["reconciled_decisions"] == 0        # denominator untouched
    assert out["counted_decisions"] == 0
    assert out["agreements"] == 0
    assert out["disagreements"] == 0
    assert out["disagreements_task_would_fire_human_held"] == 0
    assert out["pending_not_counted"] == 0         # nor is it still open


def test_supersede_does_not_reset_the_consecutive_run(monkeypatch, capsys):
    """THE POINT OF FU-180: a supersede between agreements must not break them.

    Under the pre-fix behaviour this candidate would have been reconciled with
    --held-sha, landing task_would_fire_human_held=True and zeroing the run.
    """
    _agree(monkeypatch, "%040d" % 1)
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(superseded_sha=SHA_CAND))
    _agree(monkeypatch, "%040d" % 2)
    out = _status_dict(capsys)
    assert out["consecutive_agreements"] == 2
    assert out["disagreements_task_would_fire_human_held"] == 0


def test_a_genuine_hold_still_does_reset_the_run(monkeypatch, capsys):
    """The other half of the control: the real signal must survive the fix."""
    _agree(monkeypatch, "%040d" % 1)
    _agree(monkeypatch, "%040d" % 2)
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    assert shadow_decision.reconcile(_reconcile_args(
        held_sha=SHA_CAND, evidence="chairman declined in the 22:49Z thread")) == 1
    out = _status_dict(capsys)
    assert out["consecutive_agreements"] == 0
    assert out["disagreements_task_would_fire_human_held"] == 1
    assert out["C4_met"] is False


def test_supersede_pins_the_safety_flag_to_false_explicitly(monkeypatch):
    """Absent != False. A later reader must not have to infer it (R6)."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(superseded_sha=SHA_CAND))
    (rec,) = _read_store()
    assert rec["outcome"] == "superseded"
    assert rec["task_would_fire_human_held"] is False
    assert "human_fired" not in rec               # nobody acted; claim nothing


def test_superseded_by_is_recorded_when_given(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(superseded_sha=SHA_CAND,
                                              superseded_by=SHA_OTHER))
    (rec,) = _read_store()
    assert rec["superseded_by"] == SHA_OTHER


def test_held_sha_without_evidence_is_refused(monkeypatch):
    """The cheapest verb must not be the one that fabricates a safety signal."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    assert shadow_decision.reconcile(_reconcile_args(held_sha=SHA_CAND)) == 2
    (rec,) = _read_store()
    assert rec["outcome"] == "pending"            # refusal changed nothing
    assert "task_would_fire_human_held" not in rec


def test_held_sha_with_evidence_stores_it(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(
        held_sha=SHA_CAND, evidence="declined by email 22:40Z"))
    (rec,) = _read_store()
    assert rec["hold_evidence"] == "declined by email 22:40Z"


def test_fired_and_superseded_are_mutually_exclusive(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    assert shadow_decision.reconcile(_reconcile_args(
        fired_sha=SHA_CAND, superseded_sha=SHA_CAND)) == 2
    (rec,) = _read_store()
    assert rec["outcome"] == "pending"


def test_a_superseded_decision_cannot_be_reconciled_again(monkeypatch):
    """Closed is closed: a supersede must not be laundered into an agreement."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND))
    shadow_decision.reconcile(_reconcile_args(superseded_sha=SHA_CAND))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 2
    (rec,) = _read_store()
    assert rec["outcome"] == "superseded"


def test_supersede_of_an_unknown_sha_is_not_success(monkeypatch):
    """rc 2 UNKNOWN, never 0 -- absence of a decision is not a supersede."""
    assert shadow_decision.reconcile(
        _reconcile_args(superseded_sha=SHA_OTHER)) == 2


# --------------------------------------------------------------------------
# FU-184: the decision field is a validated closed enum
#
# THE BUG THESE PIN. `record()` read the decision as
#     (a.would_fire or "").lower() in ("yes", "true", "1")
# so an ABSENT flag, a typo ("y"), or any unrecognised word became False --
# indistinguishable from a deliberate HOLD. `reconcile()` grades
# `would_fire == human_fired`, so that phantom HOLD reconciles as an AGREEMENT
# the moment the chairman also holds, and `--status` counts it toward the 10/8
# bar that gates auto-firing prod. A forgotten flag manufactured safety
# evidence. `--sha` and `--class` were both validated AND tested (see
# test_bad_sha_and_bad_class_are_refused); the primary datum of the instrument
# was neither, across all 38 pre-existing tests, because every helper defaulted
# would_fire="yes" and so no test ever supplied a bad value. An input that is
# never malformed in the fixtures cannot expose a validator that does not exist
# -- the same shape as the accept_gate fixture that omitted `mounted` entirely.
#
# NEGATIVE CONTROLS (R4). Five surgical mutants, each ast.parse-asserted to
# COMPILE before running, each run against BOTH this suite and the 38
# pre-existing tests:
#
#   A  `if decision not in DECISIONS and decision != ""`  -- an absent flag
#      passes validation and lands as a hold. THE real bug.   4 FAIL / 38 base ok
#   B  `if False and _decision(rec) == DECISION_BLOCKED`    -- blocked becomes
#      countable in _counted().                              1 FAIL / 38 base ok
#   C  same, in reconcile() -- a blocked decision gets GRADED as agree/disagree.
#                                                            3 FAIL / 38 base ok
#   D  blocked no longer has to name its precondition.       1 FAIL / 38 base ok
#   E  `_decision()` -> `return DECISION_BLOCKED`, i.e. legacy rows silently
#      REGRADED.                                3 FAIL / and 1 pre-existing FAIL
#
# E is the interesting one and it was nearly misread. It trips exactly one
# pre-existing test -- test_amend_decounts_the_live_post_hoc_agreement, which
# writes a legacy raw row and asserts it counted BEFORE the amend. That is not
# vandalism: it means "history is not regraded" is CO-OWNED, already partly
# guarded here. The distinction is one targeted base failure (co-ownership)
# versus many (vandalism), so it has to be identified by NAME, not by count.
#
# A hand re-run of E initially reported the base suite fully green, which would
# have argued E was too blunt to be a control. That reading was false: the
# PowerShell `.Replace()` used to apply the mutant had matched nothing and
# returned the original string, with rc 0 and no output. Re-run with the
# mutant's presence on disk ASSERTED before pytest was invoked, E reproduces
# 3/3. The step that verifies a control must itself fail loudly, or it will
# hand back whichever answer you were already leaning toward.
#
# THE THIRD STATE. "Safe to fire" and "authorised to fire" are different
# questions with different answers today: gates 8/8 green while FIRE_ON_GREEN's
# `rollback staged AND PROVEN` is unmet. Recording that as `no` would bank an
# agreement every time the chairman also held -- reaching 10/8 without the risky
# direction ever being exercised, which is the "attended fires measure the wrong
# artifact" defect one level down. `blocked` is excluded from BOTH counts.
# --------------------------------------------------------------------------

SHA_THIRD = "1111111111111111111111111111111111111111"


def test_absent_would_fire_is_refused_not_read_as_a_hold(monkeypatch):
    """THE headline. Pre-fix this wrote would_fire=False and returned 0."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire=None)) == 2
    assert _read_store() == []


def test_a_forgotten_flag_cannot_reach_the_c4_counter(monkeypatch, capsys):
    """End-to-end: the refused record leaves nothing that could be reconciled."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire=None)) == 2
    # rc 2 = no prior decision existed; a fire with no decision is not an
    # agreement, so the counter cannot move.
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 2
    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 0
    assert out["counted_decisions"] == 0
    assert out["agreements"] == 0


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "y", "n", "Y", "nope", "maybe", "unknown", "true", "1", "0",
     "false", "hold", "fire", "YES please"],
)
def test_unrecognised_decisions_are_refused(monkeypatch, bad):
    """Includes the former aliases `true`/`1`, deliberately tightened.

    An alias is a second spelling for the most consequential field in the
    instrument, and a second spelling is a way to record a decision you did not
    mean to make. `yes|no|blocked` and nothing else.
    """
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire=bad)) == 2
    assert _read_store() == []


def test_the_refusal_names_the_field_and_the_hazard(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(would_fire="y"))
    err = capsys.readouterr().err
    assert "--would-fire" in err
    assert "yes|no|blocked" in err
    assert "NOT a hold" in err


def test_both_real_decisions_still_record(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire="yes")) == 0
    assert shadow_decision.record(
        _rec_args_full(sha=SHA_OTHER, would_fire="no")) == 0
    recs = _read_store()
    assert [r["decision"] for r in recs] == ["yes", "no"]
    assert [r["would_fire"] for r in recs] == [True, False]


def test_surrounding_whitespace_and_case_are_tolerated(monkeypatch):
    """Tightening the enum must not make the tool brittle for real callers."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire="  YES  ")) == 0
    assert _read_store()[0]["decision"] == "yes"


# ---- the `blocked` third state ------------------------------------------

def test_blocked_requires_a_named_precondition(monkeypatch):
    """The cheapest verb must not be the one that hides its reason (FU-180)."""
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(_rec_args_full(would_fire="blocked")) == 2
    assert _read_store() == []


def test_blocked_by_without_blocked_is_refused(monkeypatch):
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(
        _rec_args_full(would_fire="yes", blocked_by="rollback_unproven")) == 2
    assert _read_store() == []


def test_blocked_records_the_clause(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    assert shadow_decision.record(
        _rec_args_full(would_fire="blocked",
                       blocked_by="rollback_staged_and_proven")) == 0
    rec = _read_store()[0]
    assert rec["decision"] == "blocked"
    assert rec["blocked_by"] == "rollback_staged_and_proven"
    assert rec["would_fire"] is False
    out = capsys.readouterr().out
    assert "BLOCKED by rollback_staged_and_proven" in out


def test_blocked_is_in_neither_c4_count(monkeypatch, capsys):
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(
        _rec_args_full(would_fire="blocked", blocked_by="rollback_unproven"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 0
    out = _status_dict(capsys)
    assert out["reconciled_decisions"] == 0
    assert out["counted_decisions"] == 0
    assert out["agreements"] == 0
    assert out["excluded_blocked_not_counted"] == 1
    assert out["disagreements_task_would_fire_human_held"] == 0


def test_blocked_does_not_reset_the_consecutive_run(monkeypatch, capsys):
    """Mirrors the supersede guarantee.

    If `blocked` were graded, a lane standing down on an authority precondition
    would zero its own run on every pass -- or, worse, bank an agreement each
    time the chairman also held and reach 10/8 having never said FIRE once.
    """
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(sha=SHA_CAND, would_fire="yes"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND)) == 0

    shadow_decision.record(_rec_args_full(sha=SHA_OTHER, would_fire="blocked",
                                          blocked_by="rollback_unproven"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_OTHER)) == 0

    shadow_decision.record(_rec_args_full(sha=SHA_THIRD, would_fire="yes"))
    assert shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_THIRD)) == 0

    out = _status_dict(capsys)
    assert out["counted_decisions"] == 2
    assert out["agreements"] == 2
    assert out["consecutive_agreements"] == 2
    assert out["excluded_blocked_not_counted"] == 1


def test_blocked_pending_is_reported_separately(monkeypatch, capsys):
    """A lane standing down for an authority reason must be VISIBLE, not merely
    absent -- otherwise it is indistinguishable from one that judged and held."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(
        _rec_args_full(would_fire="blocked", blocked_by="rollback_unproven"))
    out = _status_dict(capsys)
    assert out["pending_blocked_not_counted"] == 1
    assert out["pending_not_counted"] == 1
    assert out["counted_decisions"] == 0


def test_blocked_reconcile_prints_blocked_not_hold(monkeypatch, capsys):
    """`_decision_label` exists so a blocked record is never narrated as a
    judgement the task did not make."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(
        _rec_args_full(would_fire="blocked", blocked_by="rollback_unproven"))
    capsys.readouterr()
    shadow_decision.reconcile(_reconcile_args(fired_sha=SHA_CAND))
    out = capsys.readouterr().out
    assert "BLOCKED by rollback_unproven" in out
    assert "EXCLUDED" in out


def test_a_restage_may_correct_the_decision_to_blocked(monkeypatch, capsys):
    """A pending record counts for nothing, so making it more honest can only
    REDUCE potential credit -- never create it. That is why this is allowed and
    an `--amend` of a reconciled record still is not."""
    _prod_serving(monkeypatch, SHA_LIVE)
    shadow_decision.record(_rec_args_full(would_fire="yes"))
    assert _read_store()[0]["decision"] == "yes"
    shadow_decision.record(
        _rec_args_full(would_fire="blocked", blocked_by="rollback_unproven"))
    recs = _read_store()
    assert len(recs) == 1                      # FU-168: still one decision
    assert recs[0]["decision"] == "blocked"
    assert recs[0]["blocked_by"] == "rollback_unproven"
    assert recs[0]["restages"] == 2
    out = _status_dict(capsys)
    assert out["counted_decisions"] == 0
    assert out["pending_blocked_not_counted"] == 1


# ---- history is derived, never regraded ---------------------------------

def test_legacy_record_without_a_decision_key_is_derived_not_regraded():
    """Pre-FU-184 rows carry only the bool. They must keep meaning exactly what
    they meant -- the fix must not quietly restate history in its own favour."""
    _write_raw({"sha": SHA_LIVE, "outcome": "agreed", "would_fire": True,
                "human_fired": True, "post_hoc": False, "restages": 1})
    _write_raw({"sha": SHA_OTHER, "outcome": "agreed", "would_fire": False,
                "human_fired": False, "post_hoc": False, "restages": 1})
    recs = _read_store()
    assert shadow_decision._decision(recs[0]) == "yes"
    assert shadow_decision._decision(recs[1]) == "no"
    assert shadow_decision._counted(recs[0]) is True
    assert shadow_decision._counted(recs[1]) is True


def test_legacy_counted_totals_are_unchanged_by_the_new_enum(capsys):
    _write_raw({"sha": SHA_LIVE, "outcome": "agreed", "would_fire": True,
                "human_fired": True, "post_hoc": False, "restages": 1})
    out = _status_dict(capsys)
    assert out["counted_decisions"] == 1
    assert out["agreements"] == 1
    assert out["excluded_blocked_not_counted"] == 0


def test_an_explicit_blocked_row_is_excluded_even_when_marked_a_prediction():
    """post_hoc False alone must not be enough to count a blocked row."""
    _write_raw({"sha": SHA_LIVE, "outcome": "agreed", "decision": "blocked",
                "blocked_by": "x", "would_fire": False, "human_fired": False,
                "post_hoc": False, "restages": 1})
    assert shadow_decision._counted(_read_store()[0]) is False
