#!/usr/bin/env python3
"""Independent second opinion on the MCP axis scores, via discrete Gemini tasks.

THE QUESTION THIS SETTLES

    Three of the seven axes barely discriminate. Measured on the live bus today
    over 1,928 scored servers:

        auth_strength      83% UNKNOWN         normalised MI with overall_risk 0.02
        maintainer_trust   92% UNKNOWN_AUTHOR  normalised MI 0.06  (entropy 0.40 bits)
        network_egress     84% EXTERNAL        normalised MI 0.13

    Two hypotheses explain that, and they are currently indistinguishable:

        RUBRIC_SUSPECT    the axis asks a badly-posed question, or its label set
                          is wrong, and a competent scorer would still collapse
        EVIDENCE_STARVED  the axis is fine, but the evidence on hand cannot
                          answer it, so the scorer records "I cannot tell" as
                          though it were a score

    They have opposite fixes. The first is a rubric rewrite; the second is an
    ingestion problem and no amount of rubric work touches it.

    This module separates them by giving a DIFFERENT scorer THE SAME EVIDENCE
    and comparing. If Gemini also returns INSUFFICIENT_EVIDENCE on ~83% of
    auth_strength, the evidence is the bottleneck. If Gemini discriminates where
    the incumbent collapsed, the incumbent is under-using what it already has.

WHY THIS IS NOT A LEADERBOARD
    Gemini is not assumed to be right. It is a SECOND INSTRUMENT, and the useful
    output is where two independent instruments disagree, plus each one's own
    admitted-ignorance rate. Agreement is not proof of correctness -- both can be
    wrong the same way -- and the report says so rather than printing an accuracy.

DESIGN RULES, each of which exists because of a specific way this goes wrong

  1. BLIND. The incumbent label is never shown to Gemini. Anchoring a second
     opinion on the first produces agreement that measures nothing.

  2. ONE DISCRETE TASK PER (server, axis). Not one call scoring all seven. Seven
     axes in one prompt lets an early judgement colour the rest -- the model
     rationalises a consistent story -- and that is precisely the correlation
     this experiment is trying to MEASURE. Discrete tasks keep the axes
     independent so the measured correlation is the data's, not the prompt's.

  3. INSUFFICIENT_EVIDENCE IS A FIRST-CLASS RETURN, never folded into a risk
     label. It is the whole experiment. A scorer that must pick a risk level
     will pick one, and the resulting label carries no information while looking
     exactly like one that does. This is the same distinction referent_verify
     draws between FAIL and UNKNOWN, applied to scoring.

  4. THE EVIDENCE IS QUOTED BACK. Each verdict names which fields it actually
     used. "High risk" from a scorer that read only a package name is a
     different claim from the same words backed by a tool manifest.

  5. SAME EVIDENCE AS THE INCUMBENT, deliberately. Handing Gemini more would
     make the comparison one between two evidence sets rather than two scorers.
     On the bus that evidence is registry metadata alone -- name, description,
     url, registry_source -- because the tool-level tables carry nothing usable:
     mcp_tool_hashes has 0 rows, and mcp_fingerprints, which covers all 1,930
     scored servers, stores SHA-256("") in tool_name_hash and
     permission_scope_hash for EVERY row. The hash of nothing, indistinguishable
     from a real one.

  6. WHICH PLANE. `mcp_server_registry` and `mcp_llm_axis_scores` exist on TWO
     planes with IDENTICAL NAMES: the mesh bus (:8772) and the app Postgres.
     This module reads the BUS, which holds 1,930 scored servers. The real
     corpus is 66,565 scored servers / 465,955 score rows on the app plane
     (PLAN_200K, prod /freshness 2026-07-15) -- ~34x larger, and NOT the same
     shape: bus overall_risk is 38% in the top two bands, prod is 99.47%
     (FU-058). Any percentage this tool prints is a statement about the plane it
     read, and the report says which. Point ZO_BUS at the app plane, or set
     DATABASE_URL, before quoting a figure as a corpus figure.

Usage:
    python3 tools/axis_sense_check.py --sample 25            # pilot
    python3 tools/axis_sense_check.py --sample 25 --axis auth_strength
    python3 tools/axis_sense_check.py --dry-run              # no API calls
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BUS = os.environ.get("ZO_BUS", "http://127.0.0.1:8772")
GEMINI_MODEL = os.environ.get("ZO_GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

#: FALLBACK ONLY -- the live set is read from the bus by observed_labels().
#: These hand-written sets were the first version of this file and they were
#: WRONG in a way worth keeping as a warning: auth_strength was written
#: NONE/WEAK/MODERATE/STRONG, but the bus has only UNKNOWN/WEAK/MODERATE. Gemini
#: was therefore offered STRONG and NONE, which the incumbent can never emit,
#: so the first agreement figures were measured against a label set that does
#: not exist. That is precisely the sample-derived-enum trap PRODUCT_SPEC warns
#: about, arrived at from the other direction -- by inventing plausible labels
#: instead of reading them. Read, never guess. NOTE: `schemas/risk_axis_mapping_v1.json` -- the
#: contract PRODUCT_SPEC tells consumers to read label enums from -- DOES NOT
#: EXIST on main. So this set is sample-derived, which PRODUCT_SPEC explicitly
#: warns has burned this codebase before ("auth_strength has 4 classes, not 6").
#: It is stated here as an observation, not as a contract, and the report says
#: so. Writing that contract is a prerequisite for calling any axis collapsed:
#: an axis using 2 of 2 declared labels is fine; 2 of 6 is mode collapse, and
#: today nothing on main can tell those apart.
AXIS_LABELS: dict[str, list[str]] = {
    "auth_strength":      ["NONE", "WEAK", "MODERATE", "STRONG"],
    "capability_breadth": ["NARROW", "MODERATE", "BROAD"],
    "data_sensitivity":   ["PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL"],
    "network_egress":     ["NONE", "INTERNAL", "EXTERNAL", "ARBITRARY"],
    "maintainer_trust":   ["UNKNOWN_AUTHOR", "COMMUNITY", "ESTABLISHED"],
    "exploit_surface":    ["MINIMAL", "LIMITED", "MODERATE", "BROAD"],
    "overall_risk":       ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
}

AXIS_QUESTION = {
    "auth_strength":      "How strong is the authentication this server requires of its callers?",
    "capability_breadth": "How broad is the set of actions this server can perform?",
    "data_sensitivity":   "How sensitive is the data this server can reach?",
    "network_egress":     "Where can this server send data?",
    "maintainer_trust":   "How established and accountable is the maintainer?",
    "exploit_surface":    "How much attack surface does this server expose?",
    "overall_risk":       "Overall risk of adopting this server.",
}

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

PROMPT = """You are grading one risk axis for one MCP (Model Context Protocol) server.

AXIS: {axis}
QUESTION: {question}

ALLOWED LABELS (choose exactly one, or {insufficient}):
{labels}

EVIDENCE — this is the complete evidence available. There is no tool manifest,
no source code, and no runtime observation. Do not assume any fact not below.
{evidence}

RULES
- If the evidence above does not let you answer the question, return
  "{insufficient}". That is a correct and useful answer, not a failure. Do NOT
  guess a middle label to avoid it. A confident-looking label you cannot support
  is worse than admitting the evidence is thin.
- Judge only what is asked. Do not let a general impression of the server leak
  into an axis it does not belong to.
- "evidence_used" must list only the field names you actually relied on.

Return ONLY a JSON object, no markdown fence:
{{"label": "<one allowed label or {insufficient}>",
  "confidence": <0.0-1.0>,
  "evidence_used": ["<field names>"],
  "rationale": "<one sentence, max 25 words>"}}"""


def observed_labels() -> dict[str, list[str]]:
    """The label set each axis ACTUALLY emits, read from the bus.

    Not a contract -- an observation. `schemas/risk_axis_mapping_v1.json` does
    not exist on main, so there is nothing declared to read. That absence is the
    finding: with no declared enum, "this axis only ever emits 2 labels" cannot
    be distinguished from "this axis declares 2 labels", and those need
    different fixes.
    """
    out: dict[str, list[str]] = {}
    for r in bus_query(
            "SELECT axis_name, label, COUNT(*) AS n FROM mcp_llm_axis_scores "
            "WHERE axis_name <> 'test_axis' GROUP BY 1,2 ORDER BY 1, 3 DESC"):
        out.setdefault(r["axis_name"], []).append(r["label"])
    return out


def bus_query(sql: str, timeout: int = 60) -> list[dict]:
    req = urllib.request.Request(
        f"{BUS}/query", data=json.dumps({"sql": sql}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    rows = d.get("rows", [])
    # :8772 truncates at 200 and reports the truncated count AS the count
    # (#3997). Anything that could hit that ceiling must paginate; this module
    # keeps every query under it by construction and asserts the ceiling here so
    # a future widening cannot silently sample a partial corpus.
    if len(rows) >= 200:
        raise RuntimeError(
            f"query returned {len(rows)} rows -- at or past the :8772 "
            f"truncation ceiling (#3997). Narrow the query or paginate; do NOT "
            f"treat this as the full result.")
    return rows


def find_api_key() -> str | None:
    """Locate a Google API key in the environment without naming it in source."""
    for k, v in os.environ.items():
        if v.startswith("AIza") and len(v) > 30 and (
                "KEY" in k.upper() or "API" in k.upper() or "TOKEN" in k.upper()):
            return v
    return None


def gemini(prompt: str, key: str, retries: int = 3) -> dict | None:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192,
                             "responseMimeType": "application/json"},
    }
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            txt = d["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(),
                                     flags=re.M).strip())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return {"_error": f"HTTP {e.code}"}
        except Exception as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"_error": type(exc).__name__}
    return None


def build_evidence(s: dict) -> str:
    """Exactly what the incumbent had. No more -- see design rule 5."""
    parts = []
    for field, val in (("name", s.get("name")), ("url", s.get("url")),
                       ("registry_source", s.get("registry_source")),
                       ("description", s.get("description"))):
        v = (str(val).strip() if val else "")
        parts.append(f"- {field}: {v[:600] if v else '(absent)'}")
    return "\n".join(parts)


def sample_servers(n: int, seed: int) -> list[dict]:
    """Scored servers with registry metadata, sampled reproducibly."""
    rows = bus_query(
        "SELECT DISTINCT r.server_id, r.name, r.url, r.registry_source, "
        "r.description FROM mcp_server_registry r "
        "JOIN mcp_llm_axis_scores a ON a.server_id = r.server_id "
        "ORDER BY r.server_id LIMIT 190")
    random.Random(seed).shuffle(rows)
    return rows[:n]


def incumbent_labels(server_ids: list[str]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for i in range(0, len(server_ids), 25):
        chunk = server_ids[i:i + 25]
        ids = ",".join("'" + s.replace("'", "''") + "'" for s in chunk)
        for r in bus_query(
                "SELECT server_id, axis_name, label, p_top FROM "
                f"mcp_llm_axis_scores WHERE server_id IN ({ids})"):
            out[(r["server_id"], r["axis_name"])] = r
    return out


def run(sample: int, axes: list[str], seed: int, dry: bool,
        workers: int) -> dict:
    # Offer Gemini exactly the labels the incumbent can emit. Offering more
    # makes disagreement uninterpretable: a label the incumbent cannot produce
    # is guaranteed to read as a mismatch while proving nothing.
    live = observed_labels()
    divergence = {}
    for ax, labs in live.items():
        hand = set(AXIS_LABELS.get(ax, []))
        if hand and hand != set(labs):
            divergence[ax] = {"observed": sorted(labs),
                              "hardcoded_was": sorted(hand)}
        AXIS_LABELS[ax] = labs
    if divergence:
        print(f"label sets read from the bus; {len(divergence)} axes differed "
              f"from the hardcoded fallback (using the bus)", file=sys.stderr)
    servers = sample_servers(sample, seed)
    if not servers:
        raise SystemExit("no scored servers with registry metadata found")
    inc = incumbent_labels([s["server_id"] for s in servers])

    tasks = [(s, ax) for s in servers for ax in axes
             if (s["server_id"], ax) in inc]
    print(f"{len(servers)} servers x {len(axes)} axes = {len(tasks)} discrete "
          f"tasks (model {GEMINI_MODEL})", file=sys.stderr)
    if dry:
        print(PROMPT.format(axis=axes[0], question=AXIS_QUESTION[axes[0]],
                            insufficient=INSUFFICIENT,
                            labels="\n".join("- " + x for x in AXIS_LABELS[axes[0]]),
                            evidence=build_evidence(servers[0])))
        return {"dry_run": True, "tasks": len(tasks)}

    key = find_api_key()
    if not key:
        raise SystemExit("no Google API key found in the environment")

    def one(t):
        s, ax = t
        p = PROMPT.format(axis=ax, question=AXIS_QUESTION[ax],
                          insufficient=INSUFFICIENT,
                          labels="\n".join("- " + x for x in AXIS_LABELS[ax]),
                          evidence=build_evidence(s))
        res = gemini(p, key) or {"_error": "no response"}
        return {"server_id": s["server_id"], "name": s.get("name"),
                "axis": ax, "gemini": res,
                "incumbent": inc[(s["server_id"], ax)]["label"],
                "incumbent_p_top": inc[(s["server_id"], ax)].get("p_top")}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, tasks))
    return {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": GEMINI_MODEL, "sample": len(servers), "seed": seed,
            "label_sets": {k: v for k, v in AXIS_LABELS.items()},
            "label_set_divergence": divergence,
            "results": results}


def report(rep: dict) -> str:
    res = rep["results"]
    by = defaultdict(list)
    for r in res:
        by[r["axis"]].append(r)
    o = []
    o.append("=" * 78)
    o.append("AXIS SENSE-CHECK -- incumbent scorer vs discrete Gemini tasks")
    o.append(f"  model {rep['model']}   {rep['sample']} servers   "
             f"{len(res)} discrete tasks   seed {rep['seed']}")
    o.append("  SAME evidence as the incumbent (registry metadata only).")
    o.append("  Gemini was BLIND to the incumbent label.")
    o.append(f"  PLANE: {BUS}  -- percentages below describe THIS plane. The")
    o.append("  app Postgres plane carries the same table names and ~34x the")
    o.append("  rows, with a different distribution. See docs/AXIS_SENSE_CHECK.")
    o.append("=" * 78)
    o.append("")
    hdr = (f"{'axis':<20}{'agree':>7}{'inc UNK':>9}{'gem INSUF':>11}"
           f"{'gem labels':>12}{'errors':>8}")
    o.append(hdr)
    o.append("-" * len(hdr))
    verdicts = {}
    for ax in sorted(by):
        rs = by[ax]
        err = sum(1 for r in rs if "_error" in r["gemini"])
        ok = [r for r in rs if "_error" not in r["gemini"]]
        if not ok:
            o.append(f"{ax:<20}{'--':>7}{'--':>9}{'--':>11}{'--':>12}{err:>8}")
            continue
        gl = [str(r["gemini"].get("label", "?")) for r in ok]
        agree = sum(1 for r in ok
                    if str(r["gemini"].get("label")) == r["incumbent"])
        inc_unk = sum(1 for r in ok if "UNKNOWN" in str(r["incumbent"]).upper())
        gem_ins = sum(1 for g in gl if g == INSUFFICIENT)
        distinct = len({g for g in gl if g != INSUFFICIENT})
        o.append(f"{ax:<20}{100*agree//len(ok):>6}%{100*inc_unk//len(ok):>8}%"
                 f"{100*gem_ins//len(ok):>10}%{distinct:>12}{err:>8}")
        verdicts[ax] = (inc_unk / len(ok), gem_ins / len(ok), distinct, len(ok))
    o.append("")
    o.append("READING -- which hypothesis each axis supports")
    o.append("  EVIDENCE_STARVED      : both scorers admit ignorance at a similar")
    o.append("                          rate. The evidence cannot answer the")
    o.append("                          question. A rubric rewrite will not help;")
    o.append("                          ingestion will.")
    o.append("  INCUMBENT_OVERCONFIDENT: the incumbent labels every server while")
    o.append("                          a second scorer, on IDENTICAL evidence,")
    o.append("                          says it cannot tell for a large share.")
    o.append("                          Usually because the incumbent's label set")
    o.append("                          has no way to express ignorance.")
    o.append("  INCUMBENT_UNDERUSING   : Gemini discriminates where the incumbent")
    o.append("                          said UNKNOWN, on identical evidence.")
    o.append("  RUBRIC_SUSPECT        : both discriminate poorly without either")
    o.append("                          admitting it -- badly posed, or wrong")
    o.append("                          label set.")
    o.append("")
    for ax, (iu, gi, distinct, n) in sorted(verdicts.items()):
        # CAN THIS AXIS EVEN SAY "I DON'T KNOW"? Five of the seven label sets
        # contain no ignorance token at all, so their 0% UNKNOWN rate is a
        # property of the LABEL SET, not evidence of confidence. Keying the
        # verdict on the incumbent's UNKNOWN rate alone therefore called
        # overall_risk "DISCRIMINATING" while a second scorer declined to rate
        # 76% of the same servers -- inverting the most important row in the
        # table. The rule must ask whether ignorance was EXPRESSIBLE first.
        labs = rep.get("label_sets", {}).get(ax) or AXIS_LABELS.get(ax, [])
        expressible = any("UNKNOWN" in lb.upper() for lb in labs)
        if iu > 0.4 and gi > 0.4:
            v = "EVIDENCE_STARVED"
        elif iu > 0.4 and gi < 0.2:
            v = "INCUMBENT_UNDERUSING"
        elif gi > 0.3 and iu < 0.1:
            v = "INCUMBENT_OVERCONFIDENT"
        elif distinct <= 2 and gi < 0.2:
            v = "RUBRIC_SUSPECT"
        else:
            v = "DISCRIMINATING"
        note = "" if expressible else "   [no ignorance label exists on this axis]"
        o.append(f"  {ax:<20} {v:<24} "
                 f"inc-UNKNOWN {iu:.0%}, gemini-insufficient {gi:.0%}, "
                 f"{distinct} labels, n={n}{note}")
    o.append("")
    o.append("CAVEATS, stated because this is a small pilot")
    o.append("  - Agreement is NOT accuracy. Two instruments can be wrong the")
    o.append("    same way; only DISAGREEMENT and admitted ignorance are")
    o.append("    evidence here.")
    o.append("  - The allowed label sets are SAMPLE-DERIVED. The contract")
    o.append("    schemas/risk_axis_mapping_v1.json does not exist on main, so")
    o.append("    'uses 2 labels' cannot yet be distinguished from 'uses 2 of 2")
    o.append("    declared'. Writing that contract is a prerequisite for calling")
    o.append("    any axis collapsed.")
    o.append("=" * 78)
    return "\n".join(o)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--axis", action="append", choices=sorted(AXIS_LABELS))
    ap.add_argument("--seed", type=int, default=20260828)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--from-json", type=Path,
                    help="re-render a saved run; makes no API calls")
    a = ap.parse_args()
    if a.from_json:
        print(report(json.loads(a.from_json.read_text())))
        return 0
    rep = run(a.sample, a.axis or sorted(AXIS_LABELS), a.seed, a.dry_run,
              a.workers)
    if a.dry_run:
        return 0
    if a.json:
        a.json.write_text(json.dumps(rep, indent=2))
    print(report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
