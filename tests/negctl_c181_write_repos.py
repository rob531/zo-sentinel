"""Negative control for cycle-0181, runnable against EITHER tree.

Same world both times:
  * the bus answers information_schema with ZERO rows   (mcp_discovery_candidates absent)
  * POST /write answers 200 {"ok": true, "queued": 1}   (exactly what write_service does
    for a row it is about to discard with `Catalog Error`)

Then it asks discovery_github_paginator.write_repos([3 repos]) what it wrote.

  pre-cure (origin/main) -> written=3   RED: three rows reported, zero stored
  post-cure              -> written=0   GREEN: nothing posted, three rows spooled

Usage:  python tests/negctl_c181_write_repos.py <tree-root>
Exit 0 = the tree reports the truth.  Exit 1 = the tree reports rows it did not store.
"""

import importlib.util
import sys
import tempfile
import types
from pathlib import Path


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def load(root: Path):
    root = root.resolve()
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "dgp_under_test", root / "discovery_github_paginator.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dgp_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else ".")
    mod = load(root)

    posted = []

    def fake_post(url, json=None, timeout=None, **kw):
        if url.endswith("/query"):
            # the table does not exist: information_schema answers 200 with no rows
            return FakeResponse({"rows": [], "count": 0, "truncated": False})
        posted.append(json)
        # the enqueue receipt -- 200, and the row is discarded downstream
        return FakeResponse({"ok": True, "queued": 1}, 200)

    mod.requests = types.SimpleNamespace(post=fake_post)
    guard = sys.modules.get("bus_write_guard")
    if guard is not None:
        guard.reset_cache()
        guard._post_query = lambda u, s, t: {"rows": []}
        guard._post_write = lambda u, tb, r, to: (posted.append(r), True)[1]
        with tempfile.TemporaryDirectory() as td:
            mod.SPOOL_DIR = td
            written, errors = run(mod)
    else:
        written, errors = run(mod)

    print(f"tree={root}  write_repos -> written={written} errors={errors} posted={len(posted)}")
    if written == 0 and not posted:
        print("GREEN: the daemon reported 0 rows written and posted nothing into the void")
        return 0
    print(
        f"RED: the daemon reported {written} row(s) written and posted {len(posted)} "
        f"into a table that does not exist -- every one of them is discarded by "
        f"write_service with `Catalog Error`"
    )
    return 1


def run(mod):
    items = [
        {"full_name": f"owner/repo{i}", "html_url": f"https://x/{i}", "description": "d"}
        for i in range(3)
    ]
    return mod.write_repos(items)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
