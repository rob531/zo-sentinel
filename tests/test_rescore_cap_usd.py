"""--cap-usd: the per-wave authority ceiling is ENFORCED by the job (red team F8).

Before this, the reconciler admitted a `rescore_wave` intent on the proposer's
own `est_usd` and then ran `weekly_rescore.py --run` with no ceiling from the
grant at all. The only dollar guard inside the job was COST_CAP_USD, compared
against an ESTIMATE (quoted dph * elapsed). Memory scar
`a_ceiling_that_is_only_printed_is_not_a_ceiling`: a cap that is logged but
does not stop the spend is decoration.

So these tests assert the SIDE EFFECT -- the instance is destroyed and the
watch result is `cap_hit` -- not the log line. The negative control
(`test_cap_that_is_only_printed_fails_this_suite`) swaps `_destroy` for a
logger and proves the main assertion would go red.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"


@pytest.fixture(scope="module")
def wr():
    spec = importlib.util.spec_from_file_location("weekly_rescore_capusd", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weekly_rescore_capusd"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Run:
    def __init__(self, tmp_path, **state):
        self.dir = tmp_path
        self.state = {"run_id": "t-run", "results_branch": "rb", "instance_id": 7,
                      "phases": {}, **state}

    def save(self):
        pass

    def done(self, phase):
        return self.state["phases"].get(phase) == "done"

    def mark(self, phase, status="done", **kw):
        self.state["phases"][phase] = status
        self.state.update(kw)


def _harness(wr, monkeypatch, *, credit_now, results=("",), dph=0.30, hours=1.0):
    """Stub every network edge of ph_watch_collect; return the destroy record."""
    destroyed, ledger = [], []
    seq = list(results)
    monkeypatch.setattr(wr, "secret", lambda name: "k")
    monkeypatch.setattr(wr, "_billed_dph", lambda run, args: dph)
    monkeypatch.setattr(wr, "_results_state",
                        lambda run, pat: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(wr, "_instance_probe",
                        lambda run: {"present": True, "actual_status": "running"})
    monkeypatch.setattr(wr, "_watch_basis", lambda run, probe: "")
    monkeypatch.setattr(wr, "_pull_instance_logs", lambda run: None)
    monkeypatch.setattr(wr, "ledger", lambda ev, rid, **kw: ledger.append((ev, kw)))
    monkeypatch.setattr(wr.time, "sleep", lambda s: None)
    monkeypatch.setattr(wr.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr=""))
    if isinstance(credit_now, Exception):
        def boom():
            raise credit_now
        monkeypatch.setattr(wr, "_vast_credit", boom)
    else:
        monkeypatch.setattr(wr, "_vast_credit", lambda: credit_now)

    def fake_destroy(run, reason):
        destroyed.append(reason)
        run.state["destroyed"] = True

    monkeypatch.setattr(wr, "_destroy", fake_destroy)
    fired = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return destroyed, ledger, fired


def _args(**kw):
    base = dict(cap_usd=None, cost_cap=100.0, deadline_min=10_000, poll_secs=0,
                wedge_grace_min=10_000, max_dph=0.45)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_measured_spend_at_cap_destroys_and_prints_cap_hit(wr, monkeypatch, tmp_path, capsys):
    # estimate is $0.30 (well under the cap); MEASURED credit delta is $5.00
    destroyed, ledger, fired = _harness(wr, monkeypatch, credit_now=15.0)
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0)
    with pytest.raises(SystemExit):
        wr.ph_watch_collect(run, _args(cap_usd=2.0))
    assert destroyed == ["cap_hit"]
    assert run.state["result"] == "cap_hit"
    assert "CAP_HIT" in capsys.readouterr().out
    assert any(ev == "cap_hit" and kw["basis"] == "vast_credit_delta" for ev, kw in ledger)


def test_under_cap_does_not_fire(wr, monkeypatch, tmp_path):
    destroyed, ledger, fired = _harness(wr, monkeypatch, credit_now=19.5,
                                        results=("", "ok"))
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0)
    wr.ph_watch_collect(run, _args(cap_usd=2.0))
    assert run.state["result"] == "ok"
    assert not any(ev == "cap_hit" for ev, _ in ledger)


def test_unreadable_credit_still_binds_on_the_estimate(wr, monkeypatch, tmp_path):
    """Fail closed on the BASIS: a spend read that raises cannot lift the cap."""
    destroyed, _, fired = _harness(wr, monkeypatch, credit_now=RuntimeError("vast down"),
                                   dph=0.50, hours=5.0)          # est $2.50
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0)
    with pytest.raises(SystemExit):
        wr.ph_watch_collect(run, _args(cap_usd=2.0))
    assert destroyed == ["cap_hit"]


def test_cap_stamped_at_fire_is_enforced_by_a_later_collect(wr, monkeypatch, tmp_path):
    """--phase collect-all is a separate process; the grant must survive it."""
    destroyed, _, fired = _harness(wr, monkeypatch, credit_now=10.0)
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0, cap_usd=3.0)
    with pytest.raises(SystemExit):
        wr.ph_watch_collect(run, _args(cap_usd=None))
    assert destroyed == ["cap_hit"]


def test_no_cap_means_no_cap_hit(wr, monkeypatch, tmp_path):
    destroyed, ledger, fired = _harness(wr, monkeypatch, credit_now=0.0,
                                        results=("", "ok"))
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0)
    wr.ph_watch_collect(run, _args())
    assert not any(ev == "cap_hit" for ev, _ in ledger)


def test_cap_that_is_only_printed_fails_this_suite(wr, monkeypatch, tmp_path):
    """Negative control. Replace the destroy with a log line -- the shape of the
    scar -- and the enforcement assertion must go red."""
    destroyed, _, fired = _harness(wr, monkeypatch, credit_now=15.0)
    monkeypatch.setattr(wr, "_destroy", lambda run, reason: wr.log(f"would destroy ({reason})"))
    run = _Run(tmp_path, fired_at=fired, credit_at_launch=20.0)
    with pytest.raises(SystemExit):
        wr.ph_watch_collect(run, _args(cap_usd=2.0))
    assert destroyed == []                     # nothing was actually stopped
    assert not run.state.get("destroyed")      # ...which the main test would reject


def test_read_launch_credit_only_when_capped(wr, monkeypatch):
    monkeypatch.setattr(wr, "_vast_credit", lambda: 12.5)
    assert wr._read_launch_credit(_args()) is None
    assert wr._read_launch_credit(_args(cap_usd=2.0)) == 12.5
