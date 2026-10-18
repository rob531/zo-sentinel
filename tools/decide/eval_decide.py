#!/usr/bin/env python3
"""Eval harness for typed decisions (docs/eval/JEV_TYPED_DECISIONS_EVAL.md).

  python tools/decide/eval_decide.py --suite ui      --backend lexical
  python tools/decide/eval_decide.py --suite ui      --backend jev --debias   # needs OPENROUTER_API_KEY via AgentVault
  python tools/decide/eval_decide.py --suite recipe  --backend lexical --limit 200

ui     : 40 SYNTHETIC analyst requests, hand-labelled (view, risk, panels). Measures
         accuracy on answered rows, coverage (answered / total), expected calibration
         error, cost, latency. Synthetic -- a ceiling on what to expect, not a field number.
recipe : the builder's 530 real directives. There is NO ground truth for "right recipe",
         so this reports only AGREEMENT with goose_runner._select_recipe's keyword
         heuristic (mirrored here, not imported: goose_runner pulls the whole mesh).
         Agreement is a shadow signal, never an accuracy.

Exit codes: 0 ran; 2 refused (e.g. --backend jev without a key) -- unknown is not zero.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zo_sentinel.decide import Choice, decide  # noqa: E402
from zo_sentinel.decide.backends import GlideBackend, JevBackend, LexicalBackend  # noqa: E402
from zo_sentinel.decide.ui_compose import compose  # noqa: E402

HERE = Path(__file__).resolve().parent

# Mirror of goose_runner._select_recipe hint lists (goose_runner.py ~L653). Keep in sync.
_FRONTEND = (".html", "_dashboard.", "frontend", "_ui.", "_view.", "react")
_BACKEND = ("oauth", "login_service", "rbac", "role_enforc", "require_role", "tenant", "api_key",
            "session", "jwt", "auth_", "_auth", "org_model", "_api", "verdict", "scoring", "_consumer",
            "dashboard", "registry_search", "report", "compliance", "risk_register", "risk_trend",
            "policy", "decisions", "watchlist")
RECIPES = {
    "webapp_frontend_react": "an html page, dashboard view, frontend ui component",
    "webapp_backend_fastapi": "a fastapi api module, auth, rbac, tenant, verdict or scoring endpoint reading the database",
    "module_from_exemplar": "a standalone python module, ingestor, analyser, utility or script",
    "service_dir_from_exemplar": "a new service directory with logic router contract and service toml",
}


def heuristic_recipe(d):
    blob = " ".join(str(d.get(k, "")) for k in ("directive_id", "id", "output_file", "target_file",
                                                  "title", "description", "goal")).lower()
    if any(h in blob for h in _FRONTEND):
        return "webapp_frontend_react"
    if any(h in blob for h in _BACKEND):
        return "webapp_backend_fastapi"
    return "module_from_exemplar"  # architect.yaml default; nearest typed option


def ece(rows, bins=5):
    if not rows:
        return None
    tot, err = len(rows), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [r for r in rows if lo <= r[0] < hi or (b == bins - 1 and r[0] == 1.0)]
        if sel:
            err += len(sel) / tot * abs(statistics.mean(r[0] for r in sel) - statistics.mean(r[1] for r in sel))
    return round(err, 4)


def run_ui(backend, debias, floor):
    rows = [json.loads(l) for l in (HERE / "ui_eval_set.jsonl").read_text().splitlines() if l.strip()]
    answered = correct = risk_ok = panel_ok = 0
    calib, misses = [], []
    for r in rows:
        spec = compose(r["q"], backend, server_id=r.get("server_id"), floor=floor, debias=debias)
        ok = spec["view"] == r["view"]
        if not spec["fallback"]:
            answered += 1
            correct += ok
            calib.append((spec["confidence"]["view"], 1.0 if ok else 0.0))
        risk_ok += (spec["filters"].get("risk", "ANY") == r["risk"])
        panel_ok += (sorted(spec["panels"]) == sorted(r.get("panels", [])))
        if not ok:
            misses.append({"q": r["q"], "want": r["view"], "got": spec["view"], "fallback": spec["fallback"]})
    n = len(rows)
    return {"n": n, "coverage": round(answered / n, 3),
            "view_acc_answered": round(correct / answered, 3) if answered else None,
            "view_acc_end_to_end": round((n - len(misses)) / n, 3),
            "risk_acc": round(risk_ok / n, 3), "panel_acc": round(panel_ok / n, 3),
            "ece_view": ece(calib), "misses": misses[:12]}


def run_recipe(backend, debias, floor, limit):
    files = sorted(glob.glob(str(ROOT / "directives" / "*.json")))[:limit]
    q = {"recipe": Choice("Which build recipe fits this directive?", RECIPES, floor=floor)}
    agree = answered = 0
    confusion = {}
    for f in files:
        try:
            d = json.loads(Path(f).read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        state = {k: d.get(k) for k in ("directive_id", "title", "goal", "description", "output_file",
                                       "target_file") if d.get(k)}
        if not state:
            continue
        a = decide(state, q, backend, debias=debias)["recipe"]
        h = heuristic_recipe(d)
        if a.value is not None:
            answered += 1
            agree += a.value == h
            confusion[f"{h} -> {a.value}"] = confusion.get(f"{h} -> {a.value}", 0) + 1
    return {"n": len(files), "answered": answered,
            "agreement_with_heuristic": round(agree / answered, 3) if answered else None,
            "pairs": dict(sorted(confusion.items(), key=lambda kv: -kv[1])[:10]),
            "note": "agreement, not accuracy: no ground-truth recipe labels exist"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["ui", "recipe"], default="ui")
    ap.add_argument("--backend", choices=["lexical", "jev", "glide"], default="lexical")
    ap.add_argument("--debias", action="store_true", help="answer twice with options reversed")
    ap.add_argument("--floor", type=float, default=0.45)
    ap.add_argument("--limit", type=int, default=10_000)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.backend in ("jev", "glide"):
        be = JevBackend() if a.backend == "jev" else GlideBackend()
        if not be.available():
            print(f"REFUSED: {be.key_env} not set (inject via AgentVault)", file=sys.stderr)
            return 2
    else:
        be = LexicalBackend()
    res = run_ui(be, a.debias, a.floor) if a.suite == "ui" else run_recipe(be, a.debias, a.floor, a.limit)
    res.update({"suite": a.suite, "backend": a.backend, "debias": a.debias, "floor": a.floor})
    txt = json.dumps(res, indent=2)
    print(txt)
    if a.out:
        Path(a.out).write_text(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
