"""test_builder_stall_alarm.py -- the loud, falsifiable builder-dark alarm.

Two poles:
  * ALARM: shadow capture stale > STALE_HOURS AND work exists (pending or quarantine).
  * NOT-ALARM: shadow fresh (healthy), OR stale-but-no-work (legitimately idle).
Hermetic: tmp paths + an old mtime; no live mesh.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "tools" / "builder_stall_alarm.py"
_spec = importlib.util.spec_from_file_location("builder_stall_alarm", _SRC)
bsa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsa)


def _point(monkeypatch, tmp_path, *, stale_hours=3.0):
    shadow = tmp_path / "goose_prompt_response.jsonl"
    pend = tmp_path / "pending"
    pend.mkdir()
    monkeypatch.setattr(bsa, "SHADOW_LOG", str(shadow))
    monkeypatch.setattr(bsa, "PENDING_DIR", str(pend))
    monkeypatch.setattr(bsa, "QUARANTINE_GLOB", str(tmp_path / "*.ghost.json"))
    monkeypatch.setattr(bsa, "STALE_HOURS", stale_hours)
    monkeypatch.setattr(bsa, "FLAG", str(tmp_path / "BUILDER_STALL.alarm"))
    return shadow, pend


def _write_old(path: Path, hours_old: float):
    path.write_text("{}\n", encoding="utf-8")
    old = time.time() - hours_old * 3600
    os.utime(path, (old, old))


# --- ALARM: dark + work exists ------------------------------------------------

def test_alarm_when_dark_with_pending_work(monkeypatch, tmp_path):
    shadow, pend = _point(monkeypatch, tmp_path)
    _write_old(shadow, hours_old=10)                 # 10h since last build
    (pend / "svc_x.json").write_text("{}", encoding="utf-8")
    v = bsa.assess()
    assert v["alarm"] is True
    assert v["pending"] == 1


def test_alarm_when_dark_with_only_quarantine_backlog(monkeypatch, tmp_path):
    shadow, pend = _point(monkeypatch, tmp_path)
    _write_old(shadow, hours_old=10)
    (tmp_path / "svc_y.ghost.json").write_text("{}", encoding="utf-8")   # quarantine backlog
    v = bsa.assess()
    assert v["alarm"] is True
    assert v["quarantined"] == 1


# --- NOT-ALARM: fresh, or no work --------------------------------------------

def test_healthy_when_builds_fresh(monkeypatch, tmp_path):
    shadow, pend = _point(monkeypatch, tmp_path)
    _write_old(shadow, hours_old=0.1)                # built 6 min ago
    (pend / "svc_x.json").write_text("{}", encoding="utf-8")
    v = bsa.assess()
    assert v["alarm"] is False


def test_no_alarm_when_idle_but_no_work(monkeypatch, tmp_path):
    shadow, pend = _point(monkeypatch, tmp_path)
    _write_old(shadow, hours_old=10)                 # dark, but...
    # no pending, no quarantine -> legitimately idle, not stalled
    v = bsa.assess()
    assert v["alarm"] is False
    assert "legitimately idle" in v["reason"]


# --- flag lifecycle: written on alarm, cleared on recovery --------------------

def test_flag_written_then_cleared(monkeypatch, tmp_path):
    shadow, pend = _point(monkeypatch, tmp_path)
    (pend / "svc_x.json").write_text("{}", encoding="utf-8")
    _write_old(shadow, hours_old=10)
    bsa.run(apply=True, log=lambda *a: None)
    assert os.path.exists(bsa.FLAG)                  # alarm -> flag present
    _write_old(shadow, hours_old=0.1)                # recovers
    bsa.run(apply=True, log=lambda *a: None)
    assert not os.path.exists(bsa.FLAG)              # cleared on recovery
