"""Tests for FU-076 step-1 deterministic_signal_report (report-only).

Same two-layer convention as tests/test_vuln_surfacing.py: run the module's
__main__ self-test as a subprocess on sqlite (no network), plus seeded in-memory
assertions that the deterministic signals partition the registry while the risk
tier stays concentrated (the FU-058 contrast).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
REPO = pathlib.Path(__file__).resolve().parents[1]


def test_selftest_passes():
    env = {**os.environ, "DATABASE_URL": "sqlite://", "CLERK_PUBLISHABLE_KEY": ""}
    proc = subprocess.run(
        [sys.executable, str(REPO / "deterministic_signal_report.py")],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0 and "PASS" in out, out[-2000:]


def _seeded_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    s.add_all([
        McpServerRegistry(server_id="a", risk_tier="HIGH", verdict="risky",
                          url="https://github.com/o/a", last_scanned=now),
        McpServerRegistry(server_id="b", risk_tier="CRITICAL", verdict="risky",
                          url="http://plain.example.io/b", last_scanned=None,
                          meta=json.dumps({"stars": 9})),
        McpServerRegistry(server_id="c", risk_tier="LOW", verdict="safe",
                          url="https://gitlab.com/o/c",
                          last_scanned=now - timedelta(days=200)),
    ])
    s.commit()
    return s, now


def test_deterministic_signals_spread_and_tier_concentrates(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "")
    from deterministic_signal_report import compute_distributions, summarize
    s, now = _seeded_session()
    dist = compute_distributions(s, now=now)
    summ = summarize(dist)

    assert dist["total"] == 3
    # 2 of 3 elevated -> the risk tier stays concentrated (FU-058 shape)
    assert summ["risk_tier"]["elevated_share"] == round(2 / 3, 4)
    # deterministic signals partition the registry cleanly
    assert dist["signals"]["transport"] == {"https": 2, "http": 1}
    assert dist["signals"]["has_public_repo"] == {"true": 2, "false": 1}
    assert dist["signals"]["scan_recency"]["never"] == 1
    # community-signal coverage only where the key is actually present
    assert dist["meta_coverage"].get("stars") == 1
    assert dist["meta_coverage"].get("forks", 0) == 0


def test_empty_registry_is_safe(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from deterministic_signal_report import (compute_distributions, summarize,
                                             render_text, to_json)
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    dist = compute_distributions(s)
    assert dist["total"] == 0
    # no divide-by-zero / no raise on an empty corpus
    summarize(dist)
    assert isinstance(render_text(dist), str)
    json.loads(to_json(dist))
