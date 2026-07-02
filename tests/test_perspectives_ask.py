"""CI gate for the v1.1 Perspectives + v2 Ask feature modules.

Each module carries a REAL __main__ self-test (sqlite in-memory, seeded
heterogeneous rows, RBAC via dependency overrides, no network -- the ask
module actively asserts no socket is opened with ASK_LLM off). This suite
runs every self-test as a subprocess and requires PASS -- the same
verification semantics the autonomous builder's self-test gate applies, now
enforced on every PR.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

MODULES = [
    "facet_enum_service.py",
    "perspective_model.py",
    "perspective_query_api.py",
    "perspective_admin_api.py",
    "perspective_diff_service.py",
    "ask_corpus_indexer.py",
    "ask_retrieval_service.py",
    "ask_answer_api.py",
]


@pytest.mark.parametrize("module", MODULES)
def test_selftest_passes(module):
    env = {**os.environ, "DATABASE_URL": "sqlite://",
           "CLERK_PUBLISHABLE_KEY": ""}
    env.pop("ASK_LLM", None)
    proc = subprocess.run([sys.executable, str(REPO / module)],
                          capture_output=True, text=True, timeout=120,
                          env=env, cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"{module} rc={proc.returncode}\n{out[-2000:]}"
    assert "PASS" in out, f"{module} did not print PASS\n{out[-2000:]}"


def test_views_and_roadmap_exist_and_are_selfcontained():
    for name in ("perspective_tree_view.html", "ask_search_view.html",
                 "roadmap_announcement.html"):
        html = (REPO / name).read_text(encoding="utf-8")
        assert "<html" in html and "localStorage" not in html, name
        # no third-party CDNs beyond the app's existing Clerk pattern
        assert "cdn.jsdelivr" not in html and "unpkg.com" not in html, name


def test_routers_are_mounted_in_app_main():
    main = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    for mod in ("facet_enum_service", "perspective_admin_api",
                "perspective_query_api", "perspective_diff_service",
                "ask_corpus_indexer", "ask_answer_api"):
        assert f'"{mod}"' in main, f"{mod} missing from _OPTIONAL_ROUTERS"
    for route in ("/perspectives", "/ask", "/roadmap"):
        assert f'"{route}"' in main, f"route {route} not served"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
