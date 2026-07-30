"""FU-190 -- the daemon logged `Recorded OSV vuln` for rows that do not exist.

`ws_write` signals TOTAL failure by RETURNING None, not by raising. The caller
wrapped it in try/except, saw no exception, incremented vuln_count and logged
`Recorded OSV vuln ...`. Measured in the runtime 2026-07-30:

    18,560  log lines claiming `Recorded OSV vuln`
    55,690  HTTP 500 `no UNIQUE/PRIMARY KEY constraints` from write_service
         0  rows in mcp_threat_associations for the vuln logged as Recorded

The success message is why nobody noticed the lane had never written anything.
"""

import contextlib
import io
import os
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "threat_intel_ingestor.py")


@contextlib.contextmanager
def _no_makedirs():
    real = os.makedirs
    os.makedirs = lambda *a, **k: None
    try:
        yield
    finally:
        os.makedirs = real


def _load(src_text=None, name="tii_honesty"):
    if src_text is None:
        src_text = io.open(SRC, encoding="utf-8").read()
    mod = types.ModuleType(name)
    mod.__file__ = SRC
    with _no_makedirs():
        exec(compile(src_text, SRC, "exec"), mod.__dict__)
    return mod


@pytest.fixture(scope="module")
def src():
    return io.open(SRC, encoding="utf-8").read()


VULN = [{
    "id": "GHSA-4h5r-5jm8-jxjm",          # the exact id from the live log
    "summary": "Remote code execution in an mcp server package dependency",
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
}]


def _wire(mod, ws_write):
    mod.get_mcp_servers_for_osv_scan = lambda: [
        {"server_id": "s1", "name": "n", "url": "https://npmjs.com/package/left-pad"}
    ]
    mod.query_osv = lambda eco, pkg: (VULN if eco == "npm" else [])
    mod.threat_already_recorded = lambda *a, **k: False
    mod.logged = []
    mod.log = lambda m: mod.logged.append(str(m))
    mod.ws_write = ws_write
    return mod


def test_a_rejected_write_is_not_counted_and_not_announced():
    """The whole point: None must not become `Recorded`."""
    mod = _wire(_load(), lambda table, rows: None)      # total failure
    assert mod.process_osv_vulns() == 0, "a rejected write was counted as recorded"
    joined = "\n".join(mod.logged)
    assert "Recorded OSV vuln" not in joined, "claimed to record a row that was rejected"
    assert "NOT RECORDED" in joined
    assert "REJECTED by write_service (NOT persisted)" in joined


def test_a_successful_write_is_still_counted_and_announced():
    """Guarding the failure path must not break the success path."""
    mod = _wire(_load(), lambda table, rows: {"ok": True})
    assert mod.process_osv_vulns() == 1
    joined = "\n".join(mod.logged)
    assert "Recorded OSV vuln GHSA-4h5r-5jm8-jxjm" in joined
    assert "NOT RECORDED" not in joined
    assert "REJECTED" not in joined


def test_ws_write_logs_a_loud_total_failure(monkeypatch):
    mod = _load()
    mod.logged = []
    mod.log = lambda m: mod.logged.append(str(m))
    mod.time = types.SimpleNamespace(sleep=lambda *_: None)

    class R:
        status_code = 500
        text = ('{"detail":"Binder Error: There are no UNIQUE/PRIMARY KEY '
                'constraints that refer to this table"}')

    mod.requests = types.SimpleNamespace(post=lambda *a, **k: R())
    assert mod.ws_write("mcp_threat_associations", {"a": 1}) is None
    joined = "\n".join(mod.logged)
    assert "NOTHING PERSISTED to mcp_threat_associations" in joined
    assert "mcp_threat_associations" in joined, "failure log must name the table"


def test_negctl_prefix_reports_a_rejected_write_as_recorded(src):
    """NEGATIVE CONTROL: the pre-fix caller claims success on a None return."""
    prefix_src = src.replace(
        """                    written = ws_write('mcp_threat_associations', {""",
        """                    ws_write('mcp_threat_associations', {""", 1)
    start = prefix_src.index("                    if written is None:")
    end = prefix_src.index("                except Exception as e:", start)
    prefix_src = prefix_src[:start] + """                    vuln_count += 1
                    log(f'Recorded OSV vuln {vuln_id} for server {server_id} (severity: {severity_level})')
""" + prefix_src[end:]
    assert prefix_src != src

    mod = _wire(_load(prefix_src, "tii_honesty_prefix"), lambda table, rows: None)
    # PRE-FIX: a total write failure is counted AND announced as Recorded.
    assert mod.process_osv_vulns() == 1, "control does not reproduce the false count"
    assert "Recorded OSV vuln" in "\n".join(mod.logged), (
        "control does not reproduce the false success message"
    )
