#!/usr/bin/env python3
"""run_bakeoff.py -- the rung bake-off: same directive -> every ladder rung builds
a UI -> treewalk scores each -> ranked comparison table + bakeoff_results.json.

  # real (on tower / GH runner where keys live):
  python3 run_bakeoff.py --directive directive.txt --rungs cerebras,groq,mistral,nvidia,gemini,anthropic

  # local proof (no keys): score pre-built html files
  python3 run_bakeoff.py --local good.html broken.html
"""
import argparse, json, pathlib, sys
import treewalk as TW


def _row(name, model, build_s, html_bytes, rep, sc, err=None):
    return {"rung": name, "model": model, "build_s": build_s, "html_bytes": html_bytes,
            "score": sc, "loaded": rep.get("loaded") if rep else False,
            "interactive": rep.get("interactive_total") if rep else 0,
            "clean_clicks": (f"{rep['clicks_clean']}/{rep['clicks_attempted']}" if rep else "-"),
            "dom_mut": rep.get("dom_mutations_seen") if rep else 0,
            "cerr": len(rep["console_errors"]) if rep else 0,
            "jserr": len(rep["js_exceptions"]) if rep else 0,
            "http_err": len(rep["failed_requests"]) if rep else 0,
            "error": err}


def _table(rows):
    rows = sorted(rows, key=lambda r: (-(r["score"] or 0), r["build_s"] or 9e9))
    h = ["rank", "rung", "model", "score", "load", "inter", "clean", "domMut", "cErr", "jsErr", "http", "build_s"]
    print("\n" + "  ".join(f"{x:>7}" if i else f"{x:<4}" for i, x in enumerate(h)))
    print("-" * 96)
    for i, r in enumerate(rows, 1):
        cells = [str(i), r["rung"], (r["model"] or "")[:22], str(r["score"]),
                 "Y" if r["loaded"] else "N", str(r["interactive"]), str(r["clean_clicks"]),
                 str(r["dom_mut"]), str(r["cerr"]), str(r["jserr"]), str(r["http_err"]),
                 str(r["build_s"])]
        print(f"{cells[0]:<4}  {cells[1]:>7}  {cells[2]:>22}  {cells[3]:>5}  {cells[4]:>4}  "
              f"{cells[5]:>5}  {cells[6]:>6}  {cells[7]:>6}  {cells[8]:>4}  {cells[9]:>5}  "
              f"{cells[10]:>4}  {cells[11]:>7}")
        if r.get("error"):
            print(f"        ^ {r['error']}")
    return rows


def run_real(directive, rungs, outdir, max_clicks):
    import build_via_rung as B
    B.source_secrets()
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in rungs:
        if name not in B.RUNGS:
            rows.append(_row(name, "?", None, 0, None, 0, "unknown rung")); continue
        print(f"[build] {name} ...", flush=True)
        res = B.build(name, directive)
        if not res.get("ok"):
            rows.append(_row(name, res.get("model"), res.get("build_s"), 0, None, 0, res.get("error")))
            continue
        f = outdir / f"{name}.html"; f.write_text(res["html"])
        print(f"[treewalk] {name} ({res['html_bytes']}B) ...", flush=True)
        rep = TW.treewalk(f.resolve().as_uri(), max_clicks)
        sc, br = TW.score(rep); rep["score"], rep["score_breakdown"] = sc, br
        (outdir / f"{name}.report.json").write_text(json.dumps(rep, indent=2))
        rows.append(_row(name, res["model"], res["build_s"], res["html_bytes"], rep, sc))
    return rows


def run_local(files, max_clicks):
    rows = []
    for fp in files:
        p = pathlib.Path(fp)
        rep = TW.treewalk(p.resolve().as_uri(), max_clicks)
        sc, br = TW.score(rep); rep["score"] = sc
        rows.append(_row(p.stem, "(local file)", None, p.stat().st_size, rep, sc))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directive"); ap.add_argument("--rungs", default="cerebras,groq,mistral,nvidia,gemini,anthropic")
    ap.add_argument("--local", nargs="*"); ap.add_argument("--outdir", default="bakeoff_builds")
    ap.add_argument("--max-clicks", type=int, default=40); ap.add_argument("--out", default="bakeoff_results.json")
    a = ap.parse_args()
    if a.local:
        rows = run_local(a.local, a.max_clicks)
    else:
        if not a.directive:
            print("need --directive FILE or --local FILES", file=sys.stderr); return 2
        directive = pathlib.Path(a.directive).read_text()
        rows = run_real(directive, [r.strip() for r in a.rungs.split(",") if r.strip()], a.outdir, a.max_clicks)
    ranked = _table(rows)
    pathlib.Path(a.out).write_text(json.dumps({"ranked": ranked}, indent=2))
    win = next((r for r in ranked if r["score"]), None)
    if win:
        print(f"\nWINNER: {win['rung']} ({win['model']}) score={win['score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
