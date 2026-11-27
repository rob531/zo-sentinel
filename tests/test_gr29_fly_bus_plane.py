"""GR-29 -- code the Dockerfile ships to Fly may not address the :8772 bus.

The factory write_service (127.0.0.1:8772) is not in the Fly image, so a shipped
request path that POSTs to it can only fail there (Errno 111 -> 500, or a silent
empty result behind `except: pass`). Census 2026-11-27, tools/fly_bus_plane_check.py:
18 hits in 10 shipped files (5 mounted root routers, 4 services/active package
__init__ files, 1 dead constant) -> 0.

This test is the merge gate (it lives in the required `pytest` context): a new
shipped :8772 call is a red build. tools/ is excluded -- host-side scripts address
the bus legitimately -- and docstrings, comments and `if __name__ == "__main__"`
self-tests are not request paths.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "fly_bus_plane_check.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fly_bus_plane_check as FBP  # noqa: E402


def test_self_test_passes():
    r = subprocess.run([sys.executable, TOOL, "--self-test"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_shipped_module_addresses_the_bus():
    rc, out = FBP.run(ROOT)
    assert rc == FBP.GREEN, out


def test_detector_sees_the_shapes_that_were_fixed():
    """Negative control on the real pre-fix shapes (verbatim forms from the census)."""
    assert FBP.scan_source('WRITE_SERVICE = "http://127.0.0.1:8772"\n')
    assert FBP.scan_source('MESH_MEMORY_URL = "http://127.0.0.1:8772/query"\n')
    assert FBP.scan_source('def f(x):\n    return g(f"http://127.0.0.1:8772/{x}")\n')
    assert FBP.scan_source('def f(s):\n    s.post("http://127.0.0.1:8772/query", json={})\n')


def test_tools_is_the_only_exclusion():
    assert FBP.EXCLUDE_PREFIXES == ("tools/",)
