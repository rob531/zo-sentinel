#!/usr/bin/env python3
"""treewalk.py -- deterministic interactive UI scorer for the rung bake-off.

Drives a REAL headless browser over a built single-page UI (Selenium-class
interaction, not static DOM asserts): snapshots the accessibility tree, then
clicks every interactive node and watches what the live app does -- console
errors, uncaught JS exceptions, failed network requests, dialogs, DOM churn.
Emits a deterministic 0-100 score + a JSON report. No model-as-judge.

    python3 treewalk.py --file app.html --out report.json
    python3 treewalk.py --url http://127.0.0.1:8000 --out report.json --max-clicks 40
"""
import argparse, json, pathlib, sys


def _accessible_name(el):
    for attr in ("aria-label", "title"):
        try:
            v = el.get_attribute(attr)
            if v and v.strip():
                return v.strip()
        except Exception:
            pass
    try:
        t = (el.inner_text() or "").strip()
        if t:
            return t[:60]
    except Exception:
        pass
    return ""


def _roles(node, acc=None):
    acc = acc if acc is not None else {}
    r = node.get("role")
    if r:
        acc[r] = acc.get(r, 0) + 1
    for c in node.get("children", []) or []:
        _roles(c, acc)
    return acc


def treewalk(target_url, max_clicks=40, settle_ms=180):
    rep = {"target": target_url, "loaded": False, "title": None,
           "interactive_total": 0, "clicks_attempted": 0, "clicks_clean": 0,
           "console_errors": [], "js_exceptions": [], "failed_requests": [],
           "dialogs": [], "unnamed_interactives": 0, "dom_mutations_seen": 0, "nodes": []}
    from playwright.sync_api import sync_playwright  # lazy: scorer/tests need no browser
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: report_err(rep, m))
        page.on("pageerror", lambda e: rep["js_exceptions"].append(str(e)[:200]))
        page.on("requestfailed", lambda r: rep["failed_requests"].append(f"{r.method} {r.url[:120]} {r.failure}"))
        page.on("response", lambda r: rep["failed_requests"].append(f"HTTP {r.status} {r.url[:120]}") if r.status >= 400 else None)
        page.on("dialog", lambda d: (rep["dialogs"].append(d.message[:120]), d.dismiss()))
        try:
            page.goto(target_url, wait_until="load", timeout=15000)
            rep["loaded"] = True
            rep["title"] = page.title()
        except Exception as e:
            rep["error"] = f"load failed: {e}"
            browser.close(); return rep
        page.evaluate("""() => { window.__mut = 0;
            new MutationObserver(ms => { window.__mut += ms.length; })
              .observe(document.body, {subtree:true, childList:true, attributes:true, characterData:true}); }""")
        try:
            rep["a11y_roles"] = _roles(page.accessibility.snapshot() or {})
        except Exception:
            rep["a11y_roles"] = {}
        sel = "button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [onclick]"
        handles = page.query_selector_all(sel)
        rep["interactive_total"] = len(handles)
        for i, el in enumerate(handles[:max_clicks]):
            name = _accessible_name(el)
            if not name:
                rep["unnamed_interactives"] += 1
            entry = {"idx": i, "name": name or "(unnamed)", "tag": None, "clicked": False, "new_errors": 0}
            try:
                entry["tag"] = el.evaluate("e => e.tagName.toLowerCase()")
            except Exception:
                pass
            eb = len(rep["console_errors"]) + len(rep["js_exceptions"])
            try:
                if el.is_visible() and el.is_enabled():
                    if entry["tag"] in ("input", "textarea"):
                        el.fill("treewalk-probe", timeout=1500)
                    else:
                        el.click(timeout=1500, no_wait_after=True)
                    entry["clicked"] = True
                    rep["clicks_attempted"] += 1
                    page.wait_for_timeout(settle_ms)
            except Exception as e:
                entry["click_error"] = str(e)[:120]
            entry["new_errors"] = (len(rep["console_errors"]) + len(rep["js_exceptions"])) - eb
            if entry["clicked"] and entry["new_errors"] == 0:
                rep["clicks_clean"] += 1
            rep["nodes"].append(entry)
        try:
            rep["dom_mutations_seen"] = page.evaluate("() => window.__mut || 0")
        except Exception:
            pass
        browser.close()
    return rep


def report_err(rep, m):
    if m.type == "error":
        rep["console_errors"].append(m.text[:200])


def score(rep):
    if not rep.get("loaded"):
        return 0, {"reason": "did not load"}
    s, br = 100, {}
    inter = rep["interactive_total"]
    if inter == 0:
        s -= 35; br["no_interactive_elements"] = -35
    if inter and rep["dom_mutations_seen"] == 0:
        s -= 20; br["clicks_dont_change_dom"] = -20
    att = rep["clicks_attempted"] or 1
    pen = int(round((1 - rep["clicks_clean"] / att) * 25))
    if pen:
        s -= pen; br["unclean_clicks"] = -pen
    for k, w, cap in (("console_errors", 4, 20), ("js_exceptions", 8, 24), ("failed_requests", 5, 20)):
        n = len(rep[k])
        if n:
            d = min(n * w, cap); s -= d; br[k] = -d
    if rep["unnamed_interactives"]:
        d = min(rep["unnamed_interactives"] * 2, 10); s -= d; br["unnamed_controls"] = -d
    return max(0, s), br


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file"); g.add_argument("--url")
    ap.add_argument("--out", default="treewalk_report.json")
    ap.add_argument("--max-clicks", type=int, default=40)
    a = ap.parse_args()
    target = a.url or pathlib.Path(a.file).resolve().as_uri()
    rep = treewalk(target, a.max_clicks)
    sc, br = score(rep)
    rep["score"], rep["score_breakdown"] = sc, br
    pathlib.Path(a.out).write_text(json.dumps(rep, indent=2))
    print(f"SCORE {sc}/100  loaded={rep['loaded']} interactive={rep['interactive_total']} "
          f"clicks={rep['clicks_attempted']} clean={rep['clicks_clean']} dom_mut={rep['dom_mutations_seen']} "
          f"cerr={len(rep['console_errors'])} jserr={len(rep['js_exceptions'])} http4xx5xx={len(rep['failed_requests'])}")
    if br:
        print("penalties:", br)
    return 0


if __name__ == "__main__":
    sys.exit(main())
