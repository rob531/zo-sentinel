#!/usr/bin/env python3
"""Ground the axis scorer in the signal evidence that already exists.

THE DEFECT

    v3.0 (`model_version` v3.0_40974559) scores each axis from a server's NAME,
    URL and one-line DESCRIPTION. It has no evidence column, and five of its
    seven axes have no way to say "I cannot tell", so it emits a confident label
    for every server on every axis. On the app plane that produced 99.47% of the
    corpus in the top two risk bands (FU-058) -- a scorer that cannot express
    ignorance has to put its mass somewhere.

    Meanwhile `mcp_signal_scores` holds 10.4M rows over 3,173 servers, each with
    a score AND an evidence payload naming its source and its checks. 1,874 of
    the 1,930 scored servers have signals. The evidence to grade several axes
    was already there and unread.

THE PATTERN THIS COPIES
    The signal layer's shape, exactly: {score, evidence:{source, checks, raw}}.
    A verdict that cannot show its evidence cannot be argued with, and this
    codebase has been repairing that same shape everywhere else -- FAIL vs
    UNKNOWN in referent_verify, STALE-RED vs UNKNOWN in the bus catalog. The
    axis scorer is the last place it had not reached.

THE THREE RULES

  1. DEGENERACY IS MEASURED, NEVER ASSUMED. A signal earns the right to move a
     label by having spread ACROSS THE CORPUS THIS RUN. `tool_count` looks like
     a real signal and is not: it reads mcp_fingerprints.tool_count, and that
     table stores SHA-256("") in its hash columns for all 3,316 rows, so it
     scores 91.95 +/- 1.36 for everything. A hardcoded "trust tool_count" would
     have been correct when written and silently wrong now.

  2. NO NON-DEGENERATE BACKING SIGNAL -> INSUFFICIENT_EVIDENCE. Not a middle
     label, not the prior. This is the whole change.

  3. A DERIVED SIGNAL IS NOT EVIDENCE. `reputation` re-serves a stored trust
     value; `composite` is an aggregate of the others. Both have real spread, so
     the degeneracy test passes them, and both are circular. Grading
     maintainer_trust on `reputation` says "trusted because our trust score says
     so" -- and it MOVES NUMBERS, which is what makes it dangerous: 110 of 150
     servers came out ESTABLISHED on reputation alone, with no github_stars, and
     it read exactly like the fix working. Declared in the contract, rejected
     with a reason.

  4. THE EVIDENCE TRAVELS WITH THE LABEL. Every verdict carries the signals
     used, their scores, and their own evidence payloads.

Usage:
    python3 tools/axis_scorer_grounded.py --limit 400        # score + compare
    python3 tools/axis_scorer_grounded.py --limit 400 --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BUS = os.environ.get("ZO_BUS", "http://127.0.0.1:8772")
CONTRACT = Path(os.environ.get(
    "ZO_AXIS_CONTRACT",
    Path(__file__).resolve().parent.parent / "schemas" / "risk_axis_mapping_v1.json"))

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: A signal must vary this much across the corpus to move a label. Below it the
#: signal is a constant wearing a score's clothes.
MIN_SD = 3.0
MIN_DISTINCT = 3
#: and it must actually cover enough of the corpus to be a general instrument.
MIN_COVERAGE = 0.10

#: Evidence payloads carrying any of these are TEST DATA, not observations.
FIXTURE_MARKERS = ("fixture", "canary", "suite_id", "case")
#: Real signals score 0-100. A signal whose scores all sit in 0..1 is on a
#: different scale, which means a different writer -- and in every case measured
#: so far, a test harness.
SCALE_MAX_SUSPECT = 1.0


def q(sql: str, timeout: int = 300) -> list[dict]:
    req = urllib.request.Request(
        f"{BUS}/query", data=json.dumps({"sql": sql}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    rows = d.get("rows", [])
    if len(rows) >= 200:
        raise RuntimeError(f"{len(rows)} rows -- at the :8772 truncation "
                           f"ceiling (#3997). Paginate; do not trust this.")
    return rows


def fixture_signals() -> dict[str, str]:
    """Signals whose rows are TEST DATA wearing a production shape.

    Four signals -- permission_scope, tool_description_safety, community_signal,
    temporal_stability -- look like the highest-information signals in the store
    (sd 24-33). They are not signals at all. EVERY row carries an evidence blob
    of the form {"fixture":"mcp_scanner","suite_id":...,"case":"high_risk_isolated"}
    -- integration-suite case names -- and every score sits in 0..1 while every
    real signal is 0..100. Their apparent spread is the spread of deliberately
    varied TEST CASES.

    They were never a stalled production lane, so there was nothing to restart.
    The coverage floor already rejected them at ~1%, which was the right call
    for a weaker reason than the true one. This makes the true one explicit, so
    a future run that widened the coverage floor could not readmit them.
    """
    out: dict[str, str] = {}
    rows = q("""SELECT signal_name,
                       COUNT(*) AS n,
                       SUM(CASE WHEN evidence LIKE '%fixture%'
                                  OR evidence LIKE '%canary%'
                                  OR evidence LIKE '%suite_id%' THEN 1 ELSE 0 END) AS fixture_rows,
                       MAX(score) AS max_score
                FROM mcp_signal_scores WHERE signal_name IS NOT NULL
                GROUP BY 1""")
    for r in rows:
        why = []
        n = r["n"] or 0
        if n and (r["fixture_rows"] or 0) / n > 0.5:
            why.append(f"{r['fixture_rows']}/{n} rows carry a test-fixture "
                       f"evidence blob")
        if r["max_score"] is not None and float(r["max_score"]) <= SCALE_MAX_SUSPECT:
            why.append(f"every score <= {SCALE_MAX_SUSPECT} while real signals "
                       f"are 0-100 -- a different writer")
        if why:
            out[r["signal_name"]] = "; ".join(why)
    return out


def signal_health(total_servers: int, derived: dict[str, str],
                  fixtures: dict[str, str]) -> dict[str, dict]:
    """Which signals carry information TODAY. Rules 1, 3 and the fixture guard."""
    rows = q("""WITH latest AS (
        SELECT server_id, signal_name, score,
               ROW_NUMBER() OVER (PARTITION BY server_id, signal_name
                                  ORDER BY scored_at DESC) rn
        FROM mcp_signal_scores WHERE signal_name IS NOT NULL)
      SELECT signal_name, COUNT(*) AS servers, ROUND(AVG(score),3) AS avg,
             ROUND(STDDEV(score),3) AS sd,
             COUNT(DISTINCT ROUND(score,0)) AS distinct_scores
      FROM latest WHERE rn=1 GROUP BY 1 ORDER BY 1""")
    out = {}
    for r in rows:
        sd = float(r["sd"] or 0.0)
        cov = (r["servers"] or 0) / max(total_servers, 1)
        why = []
        is_derived = r["signal_name"] in derived
        is_fixture = r["signal_name"] in fixtures
        usable = (sd >= MIN_SD and (r["distinct_scores"] or 0) >= MIN_DISTINCT
                  and cov >= MIN_COVERAGE and not is_derived and not is_fixture)
        if is_fixture:
            why.append(f"TEST FIXTURE -- {fixtures[r['signal_name']]}")
        if is_derived:
            why.append(f"DERIVED -- {derived[r['signal_name']][:90]}")
        if sd < MIN_SD:
            why.append(f"sd {sd:.2f} < {MIN_SD} (near-constant)")
        if (r["distinct_scores"] or 0) < MIN_DISTINCT:
            why.append(f"only {r['distinct_scores']} distinct scores")
        if cov < MIN_COVERAGE:
            why.append(f"covers {cov:.1%} of the corpus")
        out[r["signal_name"]] = {
            "servers": r["servers"], "avg": r["avg"], "sd": sd,
            "distinct": r["distinct_scores"], "coverage": round(cov, 4),
            "usable": usable, "derived": is_derived, "fixture": is_fixture,
            "why_not": "; ".join(why) or None}
    return out


def latest_signals(server_ids: list[str]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for i in range(0, len(server_ids), 20):
        ids = ",".join("'" + s.replace("'", "''") + "'"
                       for s in server_ids[i:i + 20])
        for r in q(f"""WITH latest AS (
            SELECT server_id, signal_name, score, evidence, scored_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id, signal_name
                                      ORDER BY scored_at DESC) rn
            FROM mcp_signal_scores WHERE server_id IN ({ids}))
          SELECT server_id, signal_name, score, evidence FROM latest
          WHERE rn=1 AND signal_name IS NOT NULL"""):
            out[r["server_id"]][r["signal_name"]] = {
                "score": r["score"], "evidence": r["evidence"]}
    return out


#: An axis needs this much spread in its risk scores across the corpus before it
#: can claim to separate labels. Below it, the cut points are noise.
MIN_IQR = 5.0


def calibrate(axis_risks: dict[str, list[float]],
              axes: dict[str, dict]) -> dict[str, dict]:
    """Place band cut points from the CORPUS DISTRIBUTION, not from arithmetic.

    Equal-width bands on a 0-100 scale assume the scores use the scale. They do
    not. `auth_strength` is backed only by `tls_validity`, which is 50.6 +/- 4.5
    across the corpus, so every server landed in the same quarter and the axis
    emitted ONE label while looking like a four-way judgement.

    Two things this does, and one it deliberately refuses:

      - cut points at empirical quantiles, so the labels track the data
      - PUBLISHES the cut points and the IQR it derived them from, because a
        threshold nobody can see is the class of defect this repo keeps finding

      - it does NOT force a uniform spread. Quantile banding alone would put a
        fixed 25% of the corpus in CRITICAL forever, which is a different way of
        manufacturing confidence and is close to how prod reached 99.47% in the
        top two bands. If the corpus spread cannot separate N labels
        (IQR < MIN_IQR), the axis reports that it cannot band, and every verdict
        on it becomes INSUFFICIENT_EVIDENCE rather than a label the data does
        not support.
    """
    out: dict[str, dict] = {}
    for axis, spec in axes.items():
        risks = sorted(r for r in axis_risks.get(axis, []) if r is not None)
        n_lab = len([l for l in spec["labels"] if l != INSUFFICIENT])
        if len(risks) < 20:
            out[axis] = {"bandable": False, "reason":
                         f"only {len(risks)} scored servers -- too few to calibrate"}
            continue
        q1 = risks[len(risks) // 4]
        q3 = risks[(3 * len(risks)) // 4]
        iqr = q3 - q1
        if iqr < MIN_IQR:
            out[axis] = {"bandable": False, "iqr": round(iqr, 2), "reason":
                         f"IQR {iqr:.2f} < {MIN_IQR} -- the backing signals do "
                         f"not separate {n_lab} labels on this corpus"}
            continue
        cuts = [risks[int(len(risks) * i / n_lab)] for i in range(1, n_lab)]
        out[axis] = {"bandable": True, "iqr": round(iqr, 2),
                     "cuts": [round(c, 2) for c in cuts],
                     "n": len(risks),
                     "reason": f"quantile cuts over {len(risks)} servers, IQR {iqr:.2f}"}
    return out


def band(risk: float, labels: list[str], ascends_with_risk: bool,
         cal: dict | None = None) -> str:
    """risk 0..100 (100 = worst) -> a label, honouring the axis's DIRECTION.

    Five of the seven label sets ascend in risk (LOW->CRITICAL, NARROW->BROAD).
    Two ascend in GOODNESS: auth_strength is NONE->STRONG and maintainer_trust
    is UNKNOWN_AUTHOR->ESTABLISHED, so for those labels[0] is the WORST case.
    Mapping a risk score onto them without reversing labels the riskiest servers
    ESTABLISHED and the safest ones UNKNOWN_AUTHOR -- silently, and in exactly
    the direction that would make the fix look like a success.

    The direction is READ FROM THE CONTRACT, never inferred from the label
    strings: "NONE" is the safest value of network_egress and the worst value of
    auth_strength, so no amount of reading the words can settle it.
    """
    ordered = [l for l in labels if l != INSUFFICIENT]
    if not ascends_with_risk:
        ordered = list(reversed(ordered))
    if cal and cal.get("bandable"):
        idx = sum(1 for c in cal["cuts"] if risk >= c)
        return ordered[min(idx, len(ordered) - 1)]
    idx = min(int(risk / (100.0 / len(ordered))), len(ordered) - 1)
    return ordered[idx]


def score_axis(axis: str, spec: dict, sigs: dict[str, dict],
               health: dict[str, dict], cal: dict | None = None) -> dict:
    """One axis for one server. Rules 2 and 3."""
    backing = spec.get("backed_by", [])
    used, unusable, missing = [], [], []
    for name in backing:
        if name not in sigs:
            missing.append(name)
        elif not health.get(name, {}).get("usable"):
            unusable.append({"signal": name,
                             "why": health.get(name, {}).get("why_not",
                                                             "unknown signal")})
        else:
            used.append({"signal": name, "score": sigs[name]["score"],
                         "evidence": sigs[name]["evidence"]})

    if not used:
        return {"axis": axis, "label": INSUFFICIENT, "confidence": 0.0,
                "signals_used": [], "signals_unusable": unusable,
                "signals_missing": missing,
                "reason": ("no backing signal for this server carries "
                           "information: " +
                           (f"{len(missing)} absent" if missing else "") +
                           (", " if missing and unusable else "") +
                           (f"{len(unusable)} degenerate" if unusable else ""))}

    # score is 0-100 where HIGH = SAFE, so risk is the inverse.
    safety = statistics.fmean(float(u["score"]) for u in used)
    risk = 100.0 - safety
    if "labels_ascend_with_risk" not in spec:
        # FAIL LOUD. A missing direction is not a default -- guessing it is how
        # an inverted axis ships looking correct.
        raise KeyError(
            f"axis '{axis}' does not declare labels_ascend_with_risk in the "
            f"contract; refusing to guess the direction")
    if cal is not None and not cal.get("bandable", False):
        # The axis has signals, but they do not separate its labels on this
        # corpus. Saying so is the honest answer; picking one anyway is not.
        return {"axis": axis, "label": INSUFFICIENT, "confidence": 0.0,
                "risk_score": round(risk, 2), "signals_used": used,
                "signals_unusable": unusable, "signals_missing": missing,
                "reason": "not bandable: " + str(cal.get("reason"))}
    lab = band(risk, spec["labels"], spec["labels_ascend_with_risk"], cal)
    # Confidence rises with how many independent signals agree, and falls when
    # they disagree. One signal is never high confidence.
    spread = (statistics.pstdev([float(u["score"]) for u in used])
              if len(used) > 1 else 25.0)
    conf = max(0.05, min(0.95, (0.35 + 0.2 * len(used)) * (1 - min(spread, 50) / 100)))
    return {"axis": axis, "label": lab, "confidence": round(conf, 3),
            "risk_score": round(risk, 2),
            "signals_used": used, "signals_unusable": unusable,
            "signals_missing": missing,
            "reason": f"{len(used)} usable signal(s), mean safety {safety:.1f}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    contract = json.loads(CONTRACT.read_text())
    axes = contract["axes"]

    total = q("SELECT COUNT(DISTINCT server_id) AS n FROM mcp_signal_scores")[0]["n"]
    fixtures = fixture_signals()
    health = signal_health(total, contract.get("derived_signals", {}), fixtures)

    print(f"corpus for signal health: {total} servers on {BUS}", file=sys.stderr)
    print("signal usability (measured this run, rule 1):", file=sys.stderr)
    for nm, h in sorted(health.items(), key=lambda kv: -(kv[1]["sd"] or 0)):
        mark = "USABLE  " if h["usable"] else "REJECTED"
        print(f"  {mark} {str(nm):<28} sd={h['sd']:>7} cov={h['coverage']:>7.1%}"
              f"  {h['why_not'] or ''}", file=sys.stderr)

    # servers that are BOTH v3-scored and have signals -- the comparable set
    ids = [r["server_id"] for r in q(
        "SELECT DISTINCT a.server_id FROM mcp_llm_axis_scores a "
        "JOIN mcp_signal_scores s ON s.server_id = a.server_id "
        f"ORDER BY a.server_id LIMIT {min(a.limit, 190)}")]
    sigs = latest_signals(ids)

    inc: dict[tuple[str, str], str] = {}
    for i in range(0, len(ids), 25):
        idl = ",".join("'" + s.replace("'", "''") + "'" for s in ids[i:i + 25])
        for r in q("SELECT server_id, axis_name, label FROM mcp_llm_axis_scores "
                   f"WHERE server_id IN ({idl})"):
            inc[(r["server_id"], r["axis_name"])] = r["label"]

    # PASS 1 -- risk scores only, to learn each axis's distribution.
    axis_risks: dict[str, list[float]] = defaultdict(list)
    for sid in ids:
        for axis, spec in axes.items():
            v = score_axis(axis, spec, sigs.get(sid, {}), health)
            if v.get("risk_score") is not None:
                axis_risks[axis].append(v["risk_score"])
    cal = calibrate(axis_risks, axes)

    print("\nband calibration (publish the basis with the number):", file=sys.stderr)
    for axis in axes:
        c = cal[axis]
        mark = "BANDABLE    " if c.get("bandable") else "NOT BANDABLE"
        print(f"  {mark} {axis:<20} {c.get('reason')}"
              + (f"  cuts={c.get('cuts')}" if c.get("bandable") else ""),
              file=sys.stderr)

    # PASS 2 -- label with the calibrated cuts.
    results = []
    for sid in ids:
        for axis, spec in axes.items():
            v = score_axis(axis, spec, sigs.get(sid, {}), health, cal[axis])
            v["server_id"] = sid
            v["v3_label"] = inc.get((sid, axis))
            results.append(v)

    # ---- report ----------------------------------------------------------
    print()
    print("=" * 82)
    print("GROUNDED AXIS SCORER v4 vs v3.0_40974559")
    print(f"  {len(ids)} servers x {len(axes)} axes = {len(results)} verdicts")
    print(f"  plane: {BUS}  (bus plane -- NOT the 66,565-server app corpus)")
    print("=" * 82)
    print()
    hdr = (f"{'axis':<20}{'v3 UNKNOWN':>12}{'v4 INSUFF':>11}"
           f"{'v4 labels':>11}{'changed':>9}")
    print(hdr); print("-" * len(hdr))
    by = defaultdict(list)
    for r in results:
        by[r["axis"]].append(r)
    summary = {}
    for axis in axes:
        rs = by[axis]
        n = len(rs)
        v3u = sum(1 for r in rs if "UNKNOWN" in str(r["v3_label"]).upper())
        v4i = sum(1 for r in rs if r["label"] == INSUFFICIENT)
        labs = len({r["label"] for r in rs if r["label"] != INSUFFICIENT})
        chg = sum(1 for r in rs if r["label"] != r["v3_label"])
        print(f"{axis:<20}{100*v3u//n:>11}%{100*v4i//n:>10}%{labs:>11}{100*chg//n:>8}%")
        summary[axis] = {"n": n, "v3_unknown_pct": round(100*v3u/n, 1),
                         "v4_insufficient_pct": round(100*v4i/n, 1),
                         "v4_distinct_labels": labs,
                         "changed_pct": round(100*chg/n, 1)}
    print()
    print("WHAT CHANGED, and why")
    for axis in axes:
        rs = by[axis]
        n = len(rs)
        v3u = sum(1 for r in rs if "UNKNOWN" in str(r["v3_label"]).upper()) / n
        v4i = sum(1 for r in rs if r["label"] == INSUFFICIENT) / n
        ex = next((r for r in rs if r["label"] != INSUFFICIENT), rs[0])
        if v4i > 0.9:
            note = ("now ABSTAINS -- no backing signal carries information. "
                    "v3 emitted a confident label anyway.")
        elif v3u > 0.8 and v4i < 0.3:
            note = ("now DISCRIMINATES where v3 said UNKNOWN -- the evidence "
                    "existed and was unread.")
        else:
            note = "grounded in " + ", ".join(
                u["signal"] for u in ex.get("signals_used", [])) or "no signals"
        print(f"  {axis:<20} {note}")
    print("=" * 82)

    if a.json:
        a.json.write_text(json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "plane": BUS, "signal_health": health, "calibration": cal,
             "summary": summary,
             "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
