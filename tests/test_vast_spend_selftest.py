"""Collect vast_spend's self-test into the REQUIRED pytest check.

`vast_spend.py` has carried a thorough offline self-test since FU-035 -- the
fixtures, the failure paths, the API-drift mapping -- and it ran only when a
human typed `python tools/rescore/vast_spend.py selftest`. Nothing in CI ever
called it, so the module that decides whether we are allowed to spend money was
guarded by a test that no gate collected.

That is the same shape as the finding this file ships alongside: the number
existed and was correct and was never the input to anything. A test that only
runs when someone remembers is not a check, it is a habit.

Network-free: every path uses an in-module fixture or an explicit dummy key,
and the one socket it touches is 127.0.0.1:9, which must REFUSE -- that refusal
is the assertion (a network failure has to raise, not degrade to $0.00).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "vast_spend.py"


def _load():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("vast_spend", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vast_spend"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_vast_spend_selftest_passes():
    _load().selftest()


def test_burn_rate_is_a_rate_not_a_level():
    """The reading that was missing on 2026-09-01: spend read $13.21 against a
    $20 alert -- quiet -- while one live instance drew $4.21/day and put the
    alert 38 hours away. A level-only guard fires after the money is gone."""
    vs = _load()
    b = vs.burn_rate(
        {"spent": 13.21, "alert_at": 20.0, "credit": 11.79},
        [{"id": 49452453, "actual_status": "running",
          "label": "enrichment-ab-v1", "dph_total": 0.17555555555555558}],
    )
    assert b["daily_usd"] == 4.2133
    assert 38 < b["hours_to_alert"] < 39
    assert b["verdict"] == "ok"

    # NEGATIVE CONTROL -- the same call must be able to say the opposite.
    hot = vs.burn_rate({"spent": 13.21, "alert_at": 20.0, "credit": 11.79},
                       [{"id": 1, "actual_status": "running", "dph_total": 2.0}])
    assert hot["verdict"] == "alert_within_24h"


def test_an_unmeasured_fleet_is_not_an_idle_fleet():
    """R6. If the instances API cannot be read, the answer is UNKNOWN. Reporting
    $0.00/hr there would be the FU-035 defect rebuilt one derivative up."""
    vs = _load()
    try:
        vs.fetch_instances(key="x", url="http://127.0.0.1:9/instances", timeout=2)
    except vs.VastSpendError as exc:
        assert "refusing to report a burn" in str(exc)
    else:
        raise AssertionError("fetch_instances returned instead of raising")
