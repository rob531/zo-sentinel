"""test_floor_shadow_watch.py -- the read-only builder/shadow watch.

Hermetic: writes a fake shim-capture jsonl and asserts the aggregation (served-model
mix, tower(3060) count, directive_id/task fill) -- the numbers the floor-fix + M3-trial
monitoring relies on.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "tools" / "floor_shadow_watch.py"
_spec = importlib.util.spec_from_file_location("floor_shadow_watch", _SRC)
fsw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fsw)


def _write(path: Path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_served_mix_and_tower_count(tmp_path):
    log = tmp_path / "cap.jsonl"
    _write(log, [
        {"ts": "2026-10-29T18:00:00Z", "model": "hermes3:8b", "tier": "zo-ladder-low", "directive_id": "d1", "task": None},
        {"ts": "2026-10-29T18:00:01Z", "model": "MiniMax-M3", "tier": "zo-ladder-medium", "directive_id": "d2", "task": None},
        {"ts": "2026-10-29T18:00:02Z", "model": "MiniMax-M2.7", "tier": "zo-ladder-low", "directive_id": None, "task": None},
        {"ts": "2026-10-29T18:00:03Z", "model": "qwen2.5-coder:14b", "tier": "zo-ladder-medium", "directive_id": "d4", "task": None},
    ])
    r = fsw.report(path=str(log), n=400, out=lambda *a: None)
    assert r["rows"] == 4
    assert r["served"]["hermes3:8b"] == 1
    assert r["served"]["MiniMax-M3"] == 1
    assert r["tower_served"] == 2           # hermes3 + qwen2.5-coder
    assert r["directive_id"] == 3           # 3 of 4 have a directive_id
    assert r["task"] == 0


def test_missing_log_is_safe(tmp_path):
    r = fsw.report(path=str(tmp_path / "nope.jsonl"), n=10, out=lambda *a: None)
    assert r["rows"] == 0
    assert r["tower_served"] == 0
