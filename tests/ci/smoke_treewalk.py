"""smoke_treewalk.py -- the 2026-07-03 admin treewalk click-path as a
Playwright smoke against the locally-booted app (see smoke_entry.py).

Council ruling (2026-07-03): PR-time smoke proves FUNCTIONAL correctness only.
Every interaction gets a generous 10s hang-ceiling -- synthetic data cannot
prove prod performance, so there are deliberately NO tight timing budgets here.
Prod perf belongs to the nightly prod-perf job.

Steps:
  a) /app dashboard shows scored-count > 0
  b) /app/explore search returns result rows
  c) /perspectives facet tree renders (>5 groups); clicking a risk_tier option
     yields filter chip + table rows and the visible count changes
  d) selecting the saved "High & Critical risk (CI)" perspective shows N>0
  e) Trust-diff produces visible output (a no-changes message passes)
  f) /ask renders grounded results OR the honest INSUFFICIENT state
  g) /scan example config renders server cards

On any step failure: screenshot to /tmp/smoke_fail_<step>.png, exit nonzero.

NOTE: selectors target the perspectives-v12-sql-perf rewrite of
perspective_tree_view.html (#start panel, role=treeitem facet buttons, #status,
#diffOut, #saved). This smoke merges after that PR.
"""
from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8010")
STEP_TIMEOUT_MS = 10_000  # per-interaction hang ceiling (functional, not perf)

_current_step = "startup"


def fail(page, msg: str):
    path = f"/tmp/smoke_fail_{_current_step}.png"
    try:
        page.screenshot(path=path, full_page=True)
        print(f"[smoke] screenshot saved: {path}")
    except Exception as e:  # screenshot best-effort
        print(f"[smoke] screenshot failed: {e}")
    print(f"[smoke] FAIL step={_current_step}: {msg}")
    sys.exit(1)


def step(name: str):
    global _current_step
    _current_step = name
    print(f"[smoke] step {name} ...")


def parse_count(text: str) -> int:
    m = re.search(r"([\d,]+)\s+servers", text)
    if not m:
        raise AssertionError(f"no 'N servers' count in {text!r}")
    return int(m.group(1).replace(",", ""))


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        page.set_default_navigation_timeout(STEP_TIMEOUT_MS)
        expect.set_options(timeout=STEP_TIMEOUT_MS)

        try:
            # (a) dashboard: scored-count stat > 0 -----------------------------
            step("a_dashboard")
            page.goto(f"{BASE}/app")
            stat = page.locator(".stat", has_text="servers scored").first
            expect(stat).to_be_visible()
            scored = int(stat.locator(".n").inner_text().replace(",", "") or "0")
            assert scored > 0, f"dashboard scored-count is {scored}"
            print(f"[smoke]   scored={scored}")

            # (b) explore: search returns result rows --------------------------
            step("b_explore_search")
            page.goto(f"{BASE}/app/explore")
            expect(page.locator("#q")).to_be_visible()
            page.fill("#q", "smoke")
            page.get_by_role("button", name="Search").click()
            expect(page.locator("#results .row").first).to_be_visible()
            nrows = page.locator("#results .row").count()
            assert nrows > 0, "explore search returned no .row results"
            print(f"[smoke]   explore rows={nrows}")

            # (c) perspectives: tree renders; risk_tier click filters ----------
            step("c_perspectives_facets")
            page.goto(f"{BASE}/perspectives")
            expect(page.locator("#tree .facet").first).to_be_visible()
            groups = page.locator("#tree .facet").count()
            assert groups > 5, f"facet tree has only {groups} groups"
            # start state (no filters): #start panel with the indexed total
            expect(page.locator("#start")).to_be_visible()
            total_txt = page.locator("#start b").first.inner_text()
            total = int(total_txt.replace(",", ""))
            assert total > 0, "start panel shows 0 servers indexed"
            # click a risk_tier option (HIGH) in the Risk tier group
            tier_group = page.locator("#tree .facet", has=page.locator("h3", has_text="Risk tier")).first
            tier_group.get_by_role("treeitem").filter(has_text="HIGH").first.click()
            expect(page.locator("#active .chip").first).to_be_visible()
            expect(page.locator("#rows tr").first).to_be_visible()
            expect(page.locator("#status")).to_contain_text("servers")
            filtered = parse_count(page.locator("#status").inner_text())
            assert 0 < filtered < total, \
                f"filtered count {filtered} did not change vs total {total}"
            print(f"[smoke]   groups={groups} total={total} HIGH-filtered={filtered}")

            # (d) saved perspective: N servers with N > 0 ----------------------
            step("d_saved_perspective")
            page.select_option("#saved", label="High & Critical risk (CI)")
            expect(page.locator("#status")).to_contain_text("servers")
            n = parse_count(page.locator("#status").inner_text())
            assert n > 0, "saved perspective shows 0 servers"
            print(f"[smoke]   perspective servers={n}")

            # (e) trust-diff: visible output panel text ------------------------
            step("e_trust_diff")
            page.click("#diffBtn")
            expect(page.locator("#diffOut")).to_be_visible()
            expect(page.locator("#diffOut")).not_to_have_text("")
            diff_txt = page.locator("#diffOut").inner_text().strip()
            assert diff_txt, "trust-diff output is empty"
            print(f"[smoke]   diff: {diff_txt[:100]}")

            # (f) ask: grounded results OR honest INSUFFICIENT ------------------
            step("f_ask")
            page.goto(f"{BASE}/ask")
            expect(page.locator("#q")).to_be_visible()
            page.fill("#q", "high risk smoke servers with weak auth")
            page.click("#f button[type=submit]")
            expect(page.locator("#answer:visible, #insufficient:visible").first
                   ).to_be_visible()
            if page.locator("#answer").is_visible():
                assert page.locator("#answer").inner_text().strip(), "empty grounded answer"
                print("[smoke]   ask: grounded answer rendered")
            else:
                print("[smoke]   ask: honest INSUFFICIENT state rendered (accepted)")

            # (g) scan: example config renders server cards --------------------
            step("g_scan_example")
            page.goto(f"{BASE}/scan")
            expect(page.locator("#ex")).to_be_visible()
            page.click("#ex")           # "load an example"
            page.click("#scanBtn")
            expect(page.locator("#results .srv").first).to_be_visible()
            cards = page.locator("#results .srv").count()
            assert cards >= 1, "scan produced no server cards"
            print(f"[smoke]   scan cards={cards}")

        except SystemExit:
            raise
        except Exception as e:
            fail(page, f"{type(e).__name__}: {e}")
        finally:
            browser.close()

    print("[smoke] PASS: all treewalk steps green")


if __name__ == "__main__":
    main()
