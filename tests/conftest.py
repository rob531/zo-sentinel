"""Hermetic-by-default host-state isolation for the whole test suite.

WHY (2026-07-24): test_output_file_sanity failed ON THE TOWER but passed in CI --
the host's ZO_QUEUE_JANITOR=1 switched the queue janitor ON inside a hermetic
promoter test, and the unset ZO_DURABLE_QUARANTINE_DIR fell through to the REAL
durable quarantine store (/home/workspace/zo_sentinel_state/quarantine), whose
build_admin_ui_suite.failed.json retired the test's proposal before validation
could reject it. An environment-dependent test is the invisibility bug in
miniature: it degrades trust in the entire gate chain, one flaky assertion at a
time (small issues -> cascading risks).

RULE: host state never leaks into a test by default. A test that WANTS the
janitor/store sets the env or sentinel file itself (monkeypatch inside the test
overrides these autouse defaults, so opt-in tests are unaffected).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_host_state(tmp_path_factory, monkeypatch):
    # durable quarantine -> empty per-test dir, never the host store
    monkeypatch.setenv("ZO_DURABLE_QUARANTINE_DIR",
                       str(tmp_path_factory.mktemp("durable_quarantine")))
    # the host's env must not switch the janitor on inside hermetic tests
    monkeypatch.delenv("ZO_QUEUE_JANITOR", raising=False)
