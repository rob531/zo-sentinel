#!/usr/bin/env python3
"""score_validity.py -- what a VALID classifier output looks like, as code.

Chairman 2026-07-25: "shouldn't really need a canary. Just needs an
understanding of what a valid output looks like."

HISTORY
-------
For three weeks every scoring run reported success while emitting noise: the
adapter never reached the pod (.gitignore ate the weights, FU-093), so eval ran
on base Qwen2.5-3B with RANDOM HEADS. Nothing caught it because every gate
checked a PROXY for success -- row count, preds count, `degraded=false`, an
adapter sha hashed off the LOCAL disk -- and never the SEMANTIC property: does
this output look like a classifier's output at all?

    2026-07-18   86,050 servers   100.0% HIGH        <- random heads
    2026-07-21   65,045 servers   100.0% CRITICAL    <- random heads
    2026-07-24  125,731 servers   100.0% LOW         <- random heads
    2026-06-24 .. 07-03  ~1,200   MEDIUM/HIGH/LOW/CRITICAL mix  <- real

FU-108 (2026-07-26): this module then produced the MIRROR failure. Run
20260726-014732 emitted 20,576 genuinely-real, richly-discriminative scores and
the gate refused them. Three defects, all of which this revision fixes:

  A. EXTRACTION. The caller (weekly_rescore.ph_import) read labels from
     top-level `record[axis]`. The real preds shape nests them under
     `axis_pred_label`. It handed the gate ZERO rows on 20,576 valid records;
     `importable = (not bad) and bool(judged)` was then False and the run
     aborted. The gate never saw the data it condemned. An empty extraction is
     now its own verdict (EXTRACTION_FAILURE) and can never again be reported
     as "the scores are invalid" -- and extraction itself now lives HERE, so
     gate and writer cannot read different shapes (that was the whole bug).

  B. CONTRACT. AXIS_LABELS invented the enums for 4 of the 7 axes
     (data_sensitivity, network_egress, maintainer_trust, exploit_surface),
     which made every real prediction on those axes a SCHEMA_VIOLATION. The
     module's own docstring warned "never infer an enum from sample output" and
     then did exactly that. Labels below are now copied VERBATIM from the
     cross-repo contract, schemas/risk_axis_mapping_v1.json in
     rob531/zomesh-sentinel-sft, and verify_against_schema() checks them.

  C. GLOBAL INVARIANT. A single MAX_LABEL_SHARE=0.95 was applied to all seven
     axes as though they shared a population. maintainer_trust is legitimately
     ~96% UNKNOWN_AUTHOR because the registry is 464K harvested long-tail
     GitHub repos that genuinely carry no attribution signal. Repeat of the
     scar in `reachability_postmortem`: every assertion must be
     differential/local, never a global invariant. Thresholds are now per-axis,
     and any relaxation is a DECLARED, DATED, BOUNDED exception with its
     evidence attached -- never a silent bump of the global baseline.

WHAT ACTUALLY FINGERPRINTS RANDOM HEADS
---------------------------------------
Not "one axis is skewed" -- real populations are skewed. It is SIMULTANEOUS
collapse of MULTIPLE axes onto a single arbitrary class, a different class each
run (the init seed differs). One skewed axis falls to per-axis policy; two or
more collapsed axes at once is the noise signature and is reported as such.

Pure functions, stdlib only, no DB and no network -- the caller supplies rows.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

# ---- the contract ------------------------------------------------------
# VERBATIM from rob531/zomesh-sentinel-sft :: schemas/risk_axis_mapping_v1.json
# (schema_version 1.0, committed_at 2026-05-21T19:30:00Z).
# Order is significant: index == labels.index(label), matching the student's
# head output. Do NOT edit these from observed output -- edit the schema first.
AXIS_LABELS: Dict[str, Tuple[str, ...]] = {
    "overall_risk":       ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
    "auth_strength":      ("STRONG", "MODERATE", "WEAK", "UNKNOWN"),           # 4, not 6
    "capability_breadth": ("NARROW", "MODERATE", "BROAD", "UNKNOWN"),
    "data_sensitivity":   ("PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"),
    "network_egress":     ("NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"),
    "maintainer_trust":   ("ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR",
                           "SUSPICIOUS"),
    "exploit_surface":    ("MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN"),
}

# schema `off_ladder_indices` -- the "I cannot determine this" class per axis.
# Dominance by an off-ladder marker is a COVERAGE statement about the corpus,
# not a risk finding, and is judged differently from dominance by a real class.
OFF_LADDER_INDEX: Dict[str, Optional[int]] = {
    "overall_risk":       None,     # schema: off_ladder_indices == []
    "auth_strength":      3,        # UNKNOWN
    "capability_breadth": 3,        # UNKNOWN
    "data_sensitivity":   4,        # UNKNOWN
    "network_egress":     4,        # UNKNOWN
    "maintainer_trust":   3,        # UNKNOWN_AUTHOR
    "exploit_surface":    4,        # UNKNOWN
}

CANONICAL_AXIS_ORDER: Tuple[str, ...] = (
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface")

# ---- thresholds (loop-set, per the autopoiesis doctrine) ---------------
MAX_LABEL_SHARE = 0.95   # default ceiling; per-axis overrides are DECLARED below
MIN_DISTINCT_LABELS = 2  # a real classifier discriminates
MIN_ENTROPY_BITS = 0.15  # ~0 bits == collapsed to one class
MIN_COHORT = 500         # below this a uniform result can be legitimate
COLLAPSE_QUORUM = 2      # >=2 axes collapsed at once == the random-head signature

# ---- declared exceptions ----------------------------------------------
# A relaxation is only legitimate if it is (1) named, (2) dated, (3) justified
# with evidence, (4) BOUNDED so it cannot absorb a genuine regression, and
# (5) given a review date. Anything that fails a bound is still DEGENERATE.
# Passing under an exception yields VALID_DECLARED, never a silent VALID.
DECLARED_EXCEPTIONS: Dict[str, Dict[str, Any]] = {
    "maintainer_trust": {
        "declared_at": "2026-07-26",
        "declared_in": "FU-108",
        "rule": "off_ladder_dominance",
        "max_label_share": 0.99,
        "min_on_ladder_rows": 250,
        "justification": (
            "The registry is ~464K harvested long-tail GitHub repos; the large "
            "majority carry no attribution signal at all, so UNKNOWN_AUTHOR "
            "(the schema's off-ladder marker for this axis) is the CORRECT "
            "majority answer, not a collapsed head. Bounded so it cannot hide "
            "noise: the exception applies ONLY when the dominant label is this "
            "axis's designated off-ladder marker, only while the model still "
            "positively identifies >=250 on-ladder maintainers, and never above "
            "0.99 share. Collapse onto any REAL class (e.g. 100% ESTABLISHED), "
            "or loss of on-ladder discrimination, remains DEGENERATE."),
        "evidence": (
            "run 20260726-014732, n=20,576: UNKNOWN_AUTHOR 19,825 (96.4%), "
            "ESTABLISHED 746, VERIFIED 5; the other six axes scored 0.76-1.83 "
            "bits over 3-5 distinct classes in the same run -- the opposite of "
            "the cross-axis collapse that fingerprints random heads."),
        "review_by": "2026-10-26",
        "known_weakness": (
            "751 on-ladder rows out of 20,576 is thin. This axis is the least "
            "informative of the seven and is a genuine product gap, tracked "
            "separately -- the exception lets valid runs LAND, it does not "
            "claim the axis is good."),
    },
}

VALID, VALID_DECLARED, DEGENERATE, SCHEMA_VIOLATION, INSUFFICIENT, \
    EXTRACTION_FAILURE = ("VALID", "VALID_DECLARED", "DEGENERATE",
                          "SCHEMA_VIOLATION", "INSUFFICIENT", "EXTRACTION_FAILURE")

PASSING = (VALID, VALID_DECLARED)


class ExtractionFailure(RuntimeError):
    """Raised when preds exist but yielded no gradeable rows -- a CALLER bug.

    Deliberately NOT a SystemExit carrying "scores are invalid": conflating
    "I could not read the output" with "the output is bad" is precisely how
    FU-108 threw away a good $0.27 run for 12 hours.
    """


# ---- extraction: ONE shared contract -----------------------------------
def extract_axis_rows(records: Iterable[dict],
                      axes: Sequence[str] = CANONICAL_AXIS_ORDER,
                      require_parsed: bool = True) -> List[dict]:
    """preds records -> [{"axis_name":..., "label":..., "server_id":...}].

    This is the SINGLE definition of "where the labels live". The gate and the
    DB writer must both call it. FU-108 happened because they each had their
    own copy and the gate's copy read a key that does not exist.

    Tolerates the historical/alternate shapes rather than assuming one:
      {"axis_pred_label": {axis: "LABEL"}}   <- current student output
      {axis: "LABEL"}                        <- flat
      {axis: {"label": "LABEL"}}             <- nested dict
      {"axes": {axis: "LABEL"|{"label":...}}}
    Rows with axis_pred_int == -1 ("unmapped", per the schema's
    label_to_index_rule) are skipped, matching the writer.
    """
    out: List[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if require_parsed and rec.get("status") not in (None, "parsed"):
            continue
        sid = rec.get("server_id") or (rec.get("metadata") or {}).get("server_id")
        pred_label = rec.get("axis_pred_label") or {}
        pred_int = rec.get("axis_pred_int") or {}
        nested = rec.get("axes") if isinstance(rec.get("axes"), dict) else {}
        student_signals = ((rec.get("student") or {}).get("signals")
                           if isinstance(rec.get("student"), dict) else {}) or {}
        for ax in axes:
            if pred_int.get(ax) == -1:          # unmapped -- writer skips it too
                continue
            label = pred_label.get(ax)
            if label is None:
                label = rec.get(ax)
            if label is None:
                label = nested.get(ax)
            if label is None:
                label = student_signals.get(ax)
            if label is None and isinstance(rec.get("student"), dict):
                label = rec["student"].get(ax)
            if isinstance(label, dict):
                label = label.get("label")
            if label is None or label == "":
                continue
            out.append({"axis_name": ax, "label": str(label), "server_id": sid})
    return out


def verify_against_schema(schema_path: str) -> List[str]:
    """Diff AXIS_LABELS against risk_axis_mapping_v1.json. [] == in sync.

    Cheap insurance against defect B recurring: the contract drifting in the
    SFT repo while this copy silently rots.
    """
    if not os.path.exists(schema_path):
        return ["schema not found at {}".format(schema_path)]
    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    problems: List[str] = []
    seen = set()
    for entry in schema.get("axes", []):
        name = entry["name"]
        seen.add(name)
        want = tuple(entry["labels"])
        got = AXIS_LABELS.get(name)
        if got is None:
            problems.append("{}: missing from AXIS_LABELS".format(name))
        elif got != want:
            problems.append("{}: AXIS_LABELS {} != schema {}".format(name, got, want))
        if entry.get("num_classes") != len(want):
            problems.append("{}: schema num_classes {} != len(labels) {}".format(
                name, entry.get("num_classes"), len(want)))
        off = entry.get("off_ladder_indices") or []
        want_off = off[0] if off else None
        if OFF_LADDER_INDEX.get(name) != want_off:
            problems.append("{}: OFF_LADDER_INDEX {} != schema {}".format(
                name, OFF_LADDER_INDEX.get(name), want_off))
    for extra in sorted(set(AXIS_LABELS) - seen):
        problems.append("{}: in AXIS_LABELS but not in schema".format(extra))
    return problems


def off_ladder_label(axis: str) -> Optional[str]:
    idx = OFF_LADDER_INDEX.get(axis)
    labels = AXIS_LABELS.get(axis)
    if idx is None or not labels or idx >= len(labels):
        return None
    return labels[idx]


def entropy_bits(counts: Iterable[int]) -> float:
    """Shannon entropy of a label histogram. Collapsed distribution -> ~0."""
    c = [n for n in counts if n > 0]
    total = sum(c)
    if total <= 0:
        return 0.0
    return -sum((n / total) * math.log2(n / total) for n in c)


def validate_axis(axis: str, labels: Sequence[str],
                  min_cohort: int = MIN_COHORT) -> dict:
    """Verdict for ONE axis over a cohort of predicted labels.

    Thin facade: it counts the labels and defers to validate_axis_counts,
    where the rules actually live.
    """
    return validate_axis_counts(axis, Counter(labels), min_cohort)


def validate_axis_counts(axis: str, histogram: Mapping[str, int],
                         min_cohort: int = MIN_COHORT) -> dict:
    """Verdict for ONE axis from a label HISTOGRAM {label: count}.

    SCHEMA_VIOLATION beats every other verdict: a label outside the contract
    means we are not even looking at this classifier's output.

    This is THE implementation -- there is deliberately no second copy of the
    collapse rules. Gate and caller running different code for the same
    question is the FU-108 defect class. Counting is also the only tractable
    entry point at moat scale: the per-cohort trust audit in
    weekly_rescore.ph_export judges ~1.9M stored score rows from a single
    GROUP BY and must never expand them into a list of dicts.
    """
    hist = Counter({str(k): int(v) for k, v in histogram.items() if int(v) > 0})
    n = sum(hist.values())
    allowed = AXIS_LABELS.get(axis)
    unknown_labels = sorted(set(hist) - set(allowed)) if allowed else []
    if allowed and unknown_labels:
        return {"axis": axis, "n": n, "verdict": SCHEMA_VIOLATION,
                "offending_labels": unknown_labels, "histogram": dict(hist)}
    if n == 0:
        return {"axis": axis, "n": 0, "verdict": INSUFFICIENT,
                "reason": "no rows", "histogram": {}}
    top_label, top_n = hist.most_common(1)[0]
    share = top_n / n
    bits = entropy_bits(hist.values())
    marker = off_ladder_label(axis)
    on_ladder = sum(c for lb, c in hist.items() if lb != marker)
    base = {"axis": axis, "n": n, "top_label": top_label,
            "top_share": round(share, 4), "distinct_labels": len(hist),
            "entropy_bits": round(bits, 4), "on_ladder_rows": on_ladder,
            "histogram": dict(hist)}
    if n < min_cohort:
        # Too small to judge: say so. Never let a small cohort PASS as proof,
        # and never condemn it either -- absence of evidence is a finding.
        return {**base, "verdict": INSUFFICIENT,
                "reason": "cohort {} < {}".format(n, min_cohort)}

    collapsed = (share > MAX_LABEL_SHARE or len(hist) < MIN_DISTINCT_LABELS
                 or bits < MIN_ENTROPY_BITS)
    if not collapsed:
        return {**base, "verdict": VALID}

    # Over the default ceiling. Does a DECLARED, BOUNDED exception cover it?
    exc = DECLARED_EXCEPTIONS.get(axis)
    if (exc and exc.get("rule") == "off_ladder_dominance"
            and marker is not None
            and top_label == marker                        # bound 1: off-ladder only
            and share <= exc["max_label_share"]            # bound 2: hard ceiling
            and on_ladder >= exc["min_on_ladder_rows"]     # bound 3: still discriminates
            and len(hist) >= MIN_DISTINCT_LABELS):         # bound 4: not single-class
        return {**base, "verdict": VALID_DECLARED,
                "declared": {k: exc[k] for k in
                             ("declared_at", "declared_in", "rule", "review_by")},
                "reason": ("{} is the schema off-ladder marker and owns {:.1%}; "
                           "{} on-ladder predictions retain discrimination. "
                           "Declared {} in {}, review by {}.").format(
                               marker, share, on_ladder, exc["declared_at"],
                               exc["declared_in"], exc["review_by"])}
    return {**base, "verdict": DEGENERATE, "collapsed": True,
            "reason": ("{} owns {:.1%} of {} rows, {} distinct, {:.3f} bits"
                       " -- the random-head fingerprint").format(
                           top_label, share, n, len(hist), bits)}


def validate_run(rows: Iterable[dict], min_cohort: int = MIN_COHORT) -> dict:
    """Gate a whole scoring run. rows: {"axis_name":..., "label":...}.

    Thin facade: it counts the rows and defers to validate_run_from_histogram.
    """
    counted: Dict[str, Counter] = {}
    for r in rows:
        counted.setdefault(r["axis_name"], Counter())[r["label"]] += 1
    return validate_run_from_histogram(counted, min_cohort)


def validate_run_from_histogram(histograms: Mapping[str, Mapping[str, int]],
                                min_cohort: int = MIN_COHORT) -> dict:
    """Gate a whole scoring run from {axis_name: {label: count}}.

    A run is IMPORTABLE only if no axis is DEGENERATE or SCHEMA_VIOLATION and
    at least one axis was actually judgeable. INSUFFICIENT never authorises an
    import on its own. An empty histogram is EXTRACTION_FAILURE, not invalidity.

    Takes COUNTS rather than rows so a caller holding a
    `GROUP BY axis_name, label` result -- e.g. the per-cohort trust audit
    weekly_rescore runs over the entire moat before choosing a refresh order --
    reaches the same verdict through the same code without materialising
    millions of dicts.
    """
    by_axis = {a: Counter({str(lb): int(c) for lb, c in h.items() if int(c) > 0})
               for a, h in histograms.items()}
    by_axis = {a: h for a, h in by_axis.items() if h}
    if not by_axis:
        return {"axes": [], "n_axes": 0, "n_valid": 0, "n_bad": 0,
                "importable": False, "verdict": EXTRACTION_FAILURE,
                "n_collapsed": 0, "random_head_signature": False}
    axes = [validate_axis_counts(a, h, min_cohort)
            for a, h in sorted(by_axis.items())]
    bad = [a for a in axes if a["verdict"] in (DEGENERATE, SCHEMA_VIOLATION)]
    judged = [a for a in axes if a["verdict"] in PASSING]
    collapsed = [a for a in axes if a.get("collapsed")]
    return {"axes": axes,
            "n_axes": len(axes),
            "n_valid": len(judged),
            "n_bad": len(bad),
            "n_collapsed": len(collapsed),
            # >=2 axes collapsed simultaneously is the noise signature, and is
            # a materially different diagnosis from one legitimately-skewed axis
            "random_head_signature": len(collapsed) >= COLLAPSE_QUORUM,
            "importable": (not bad) and bool(judged),
            "verdict": (SCHEMA_VIOLATION
                        if any(a["verdict"] == SCHEMA_VIOLATION for a in bad)
                        else DEGENERATE if bad
                        else VALID if judged else INSUFFICIENT)}


def assert_importable(rows: Iterable[dict], min_cohort: int = MIN_COHORT,
                      source_records: Optional[int] = None) -> dict:
    """Fail-CLOSED import gate. Raises on anything not VALID/VALID_DECLARED.

    Wire this in front of the import phase: garbage then cannot reach the moat,
    no matter what the row counts or `degraded` flag say. Each failure class
    raises a DISTINCT, actionable message -- "I could not read your output" and
    "your output is noise" are different incidents with different owners.
    """
    rows = list(rows)
    report = validate_run(rows, min_cohort)

    if report["verdict"] == EXTRACTION_FAILURE:
        raise ExtractionFailure(
            "ABORT import: extracted 0 gradeable rows from {} source record(s). "
            "This is a CALLER/shape defect, NOT a verdict on the scores -- do "
            "not discard the run. Check that labels are being read via "
            "score_validity.extract_axis_rows (FU-108).".format(
                "unknown" if source_records is None else source_records))

    if not report["importable"]:
        detail = "; ".join(
            "{}: {} ({})".format(a["axis"], a["verdict"],
                                 a.get("reason") or a.get("offending_labels"))
            for a in report["axes"] if a["verdict"] not in PASSING)
        if report["random_head_signature"]:
            head = ("ABORT import: RANDOM-HEAD SIGNATURE -- {} axes collapsed "
                    "simultaneously. The adapter almost certainly did not attach "
                    "(cf. FU-093). Check the adapter reached the pod before "
                    "re-firing.").format(report["n_collapsed"])
        elif report["verdict"] == SCHEMA_VIOLATION:
            head = ("ABORT import: labels outside the contract. Either the "
                    "student changed or AXIS_LABELS has drifted from "
                    "schemas/risk_axis_mapping_v1.json -- reconcile the SCHEMA "
                    "first, never edit the enum to match observed output "
                    "(FU-108 defect B).")
        else:
            head = ("ABORT import: scores are not valid classifier output "
                    "[{}]".format(report["verdict"]))
        raise SystemExit(
            "{} -- {}. Refusing to write garbage to the moat "
            "(FU-093/FU-094/FU-108).".format(head, detail))
    return report


def format_report(report: dict) -> str:
    """One-line-per-axis summary for logs. Declared passes are marked."""
    return ", ".join(
        "{}={}{}({:.0%} top, {:.2f} bits)".format(
            a["axis"], a["verdict"],
            "!" if a["verdict"] == VALID_DECLARED else "",
            a.get("top_share", 0.0), a.get("entropy_bits", 0.0))
        for a in report.get("axes", []))


if __name__ == "__main__":
    # Fixtures are the REAL observed prod distributions -- this suite is a
    # regression test against the actual incidents, both directions:
    # the 3 garbage waves MUST be rejected, and the FU-108 run MUST land.
    def rows(axis, hist):
        return [{"axis_name": axis, "label": l}
                for l, n in hist.items() for _ in range(n)]

    def labels(axis, hist):
        return [r["label"] for r in rows(axis, hist)]

    # --- the three garbage waves MUST still be rejected ---
    g0724 = validate_axis("overall_risk", labels("overall_risk",
                                                 {"LOW": 125726, "CRITICAL": 5}))
    assert g0724["verdict"] == DEGENERATE, g0724
    g0721 = validate_axis("overall_risk", ["CRITICAL"] * 65045)
    assert g0721["verdict"] == DEGENERATE and g0721["distinct_labels"] == 1, g0721
    g0718 = validate_axis("overall_risk", labels("overall_risk",
                                                 {"HIGH": 86049, "CRITICAL": 1}))
    assert g0718["verdict"] == DEGENERATE, g0718
    # collapse onto WEAK: dominant label is NOT the off-ladder marker (UNKNOWN),
    # so no exception can apply
    ga = validate_axis("auth_strength", labels("auth_strength",
                                               {"WEAK": 125723, "UNKNOWN": 8}))
    assert ga["verdict"] == DEGENERATE, ga

    # --- real, mixed output MUST pass ---
    real = validate_axis("overall_risk", labels(
        "overall_risk", {"MEDIUM": 910, "HIGH": 300, "LOW": 140, "CRITICAL": 70}))
    assert real["verdict"] == VALID, real
    assert real["entropy_bits"] > MIN_ENTROPY_BITS

    # --- schema violation beats everything (a label off-contract) ---
    sv = validate_axis("auth_strength", ["STRONG"] * 600 + ["TOTALLY_BOGUS"] * 600)
    assert sv["verdict"] == SCHEMA_VIOLATION, sv
    assert sv["offending_labels"] == ["TOTALLY_BOGUS"], sv
    # auth_strength is 4 classes -- a 6-class assumption is how noise passes as schema
    assert len(AXIS_LABELS["auth_strength"]) == 4
    # FU-108 defect B: the four axes whose enums were previously INVENTED
    assert AXIS_LABELS["data_sensitivity"] == (
        "PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN")
    assert AXIS_LABELS["network_egress"] == (
        "NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN")
    assert AXIS_LABELS["maintainer_trust"] == (
        "ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR", "SUSPICIOUS")
    assert AXIS_LABELS["exploit_surface"] == (
        "MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN")
    assert off_ladder_label("maintainer_trust") == "UNKNOWN_AUTHOR"
    assert off_ladder_label("overall_risk") is None

    # --- small cohorts are INSUFFICIENT: never a free pass, never condemned ---
    small = validate_axis("overall_risk", ["LOW"] * 10)
    assert small["verdict"] == INSUFFICIENT, small

    # --- FU-108 defect A: extraction from the REAL preds shape --------------
    real_shape = [{
        "server_id": "4d9ab2614e7b96f2", "status": "parsed",
        "axis_pred_label": {"overall_risk": "MEDIUM", "auth_strength": "UNKNOWN",
                            "capability_breadth": "NARROW",
                            "data_sensitivity": "PUBLIC",
                            "network_egress": "UNKNOWN",
                            "maintainer_trust": "UNKNOWN_AUTHOR",
                            "exploit_surface": "LIMITED"},
        "axis_pred_int": {"overall_risk": 1, "auth_strength": 3,
                          "capability_breadth": 0, "data_sensitivity": 0,
                          "network_egress": 4, "maintainer_trust": 3,
                          "exploit_surface": 1}}]
    ex = extract_axis_rows(real_shape)
    assert len(ex) == 7, ex
    assert {r["axis_name"] for r in ex} == set(CANONICAL_AXIS_ORDER)
    assert all(r["server_id"] == "4d9ab2614e7b96f2" for r in ex)
    # the OLD caller read top-level record[axis] -- proof it extracted nothing
    assert all(real_shape[0].get(a) is None for a in CANONICAL_AXIS_ORDER)
    # alternate shapes still work
    assert len(extract_axis_rows([{"overall_risk": "LOW"}], ["overall_risk"])) == 1
    assert len(extract_axis_rows(
        [{"overall_risk": {"label": "LOW"}}], ["overall_risk"])) == 1
    assert len(extract_axis_rows(
        [{"axes": {"overall_risk": "LOW"}}], ["overall_risk"])) == 1
    # unmapped (-1) is skipped, matching the writer
    assert extract_axis_rows([{"axis_pred_label": {"overall_risk": "LOW"},
                               "axis_pred_int": {"overall_risk": -1}}],
                             ["overall_risk"]) == []

    # --- empty extraction is EXTRACTION_FAILURE, never "invalid scores" -----
    empty = validate_run([])
    assert empty["verdict"] == EXTRACTION_FAILURE and not empty["importable"], empty
    raised_kind = None
    try:
        assert_importable([], source_records=20576)
    except ExtractionFailure as e:
        raised_kind = ("extraction", str(e))
    except SystemExit as e:                      # the FU-108 misdiagnosis
        raised_kind = ("systemexit", str(e))
    assert raised_kind and raised_kind[0] == "extraction", raised_kind
    assert "NOT a verdict on the scores" in raised_kind[1]

    # --- FU-108 defect C: declared, BOUNDED off-ladder exception ------------
    mt = validate_axis("maintainer_trust", labels(
        "maintainer_trust",
        {"UNKNOWN_AUTHOR": 19825, "ESTABLISHED": 746, "VERIFIED": 5}))
    assert mt["verdict"] == VALID_DECLARED, mt
    assert mt["on_ladder_rows"] == 751, mt
    # bound 1: collapse onto a REAL class is still DEGENERATE
    mt_real = validate_axis("maintainer_trust", labels(
        "maintainer_trust", {"ESTABLISHED": 19825, "VERIFIED": 5}))
    assert mt_real["verdict"] == DEGENERATE, mt_real
    # bound 3: off-ladder dominance WITHOUT on-ladder discrimination is noise
    mt_noise = validate_axis("maintainer_trust", labels(
        "maintainer_trust", {"UNKNOWN_AUTHOR": 20500, "ESTABLISHED": 10}))
    assert mt_noise["verdict"] == DEGENERATE, mt_noise
    # bound 2: total collapse onto the marker is still DEGENERATE
    mt_all = validate_axis("maintainer_trust", ["UNKNOWN_AUTHOR"] * 20576)
    assert mt_all["verdict"] == DEGENERATE, mt_all
    # an axis with no declared exception gets no relief
    assert validate_axis("network_egress", labels(
        "network_egress", {"UNKNOWN": 20000, "NONE": 100}))["verdict"] == DEGENERATE

    # --- run-level: the FU-108 wave (real observed histograms) MUST import ---
    fu108 = []
    for ax, hist in [
        ("overall_risk", {"MEDIUM": 14103, "HIGH": 5002, "CRITICAL": 1017, "LOW": 454}),
        ("auth_strength", {"UNKNOWN": 17315, "WEAK": 2408, "MODERATE": 853}),
        ("capability_breadth", {"MODERATE": 12216, "BROAD": 5129, "NARROW": 3231}),
        ("data_sensitivity", {"SENSITIVE": 10951, "INTERNAL": 4002,
                              "CRITICAL": 2581, "PUBLIC": 2434, "UNKNOWN": 608}),
        ("network_egress", {"EXTERNAL": 17254, "ARBITRARY": 1768, "UNKNOWN": 1455,
                            "NONE": 67, "INTERNAL": 32}),
        ("maintainer_trust", {"UNKNOWN_AUTHOR": 19825, "ESTABLISHED": 746,
                              "VERIFIED": 5}),
        ("exploit_surface", {"MODERATE": 12943, "BROAD": 3728, "LIMITED": 3466,
                             "MINIMAL": 439})]:
        fu108 += rows(ax, hist)
    r108 = validate_run(fu108)
    assert r108["importable"] is True, format_report(r108)
    assert r108["n_axes"] == 7 and r108["n_valid"] == 7, r108
    assert r108["random_head_signature"] is False, r108
    assert_importable(fu108)

    # --- counts entry point: SAME verdicts, no rows materialised ------------
    # This is what weekly_rescore.cohort_trust() calls, once, over the whole
    # moat (~1.9M rows -> a few hundred GROUP BY rows), to decide which
    # scored_at cohorts the refresh lane must revisit FIRST.
    hist_from_rows: Dict[str, Counter] = {}
    for _r in fu108:
        hist_from_rows.setdefault(_r["axis_name"], Counter())[_r["label"]] += 1
    h108 = validate_run_from_histogram(hist_from_rows)
    assert h108 == r108, (h108, r108)
    assert h108["verdict"] == VALID and h108["importable"] is True, h108
    # the two paths agree axis-by-axis as well as run-wide
    assert (validate_axis_counts("overall_risk", {"LOW": 125726, "CRITICAL": 5})
            == validate_axis("overall_risk", labels(
                "overall_risk", {"LOW": 125726, "CRITICAL": 5}))), "paths diverge"
    # a real garbage cohort, judged straight from a GROUP BY-shaped histogram:
    # DEGENERATE == distrusted == goes to the FRONT of the refresh queue
    garbage_cohort = {"overall_risk": {"LOW": 125726, "CRITICAL": 5},
                      "auth_strength": {"WEAK": 125723, "UNKNOWN": 8}}
    gh = validate_run_from_histogram(garbage_cohort)
    assert gh["verdict"] == DEGENERATE and gh["importable"] is False, gh
    assert gh["random_head_signature"] is True and gh["n_collapsed"] == 2, gh
    # tiny historical cohorts are INSUFFICIENT -- NOT distrusted, so they must
    # not jump the queue ahead of provable garbage
    assert validate_run_from_histogram(
        {"overall_risk": {"LOW": 4, "HIGH": 3}})["verdict"] == INSUFFICIENT
    # empty / all-zero histograms are EXTRACTION_FAILURE, never "invalid"
    assert validate_run_from_histogram({})["verdict"] == EXTRACTION_FAILURE
    assert validate_run_from_histogram(
        {"overall_risk": {"LOW": 0}})["verdict"] == EXTRACTION_FAILURE

    # --- run-level: garbage still fails, and is DIAGNOSED as random heads ---
    bad_run = validate_run(rows("overall_risk", {"LOW": 125726, "CRITICAL": 5}))
    assert bad_run["importable"] is False and bad_run["verdict"] == DEGENERATE
    multi = (rows("overall_risk", {"LOW": 125726, "CRITICAL": 5})
             + rows("auth_strength", {"WEAK": 125723, "UNKNOWN": 8}))
    mr = validate_run(multi)
    assert mr["random_head_signature"] is True and mr["n_collapsed"] == 2, mr
    raised = False
    try:
        assert_importable(multi)
    except SystemExit as e:
        raised = "RANDOM-HEAD SIGNATURE" in str(e)
    assert raised, "assert_importable failed to fail-closed on the noise signature"

    # --- contract drift check against the SFT schema, when available --------
    _schema = os.environ.get("RISK_AXIS_SCHEMA")
    if _schema:
        _p = verify_against_schema(_schema)
        assert not _p, "AXIS_LABELS has drifted from the schema: {}".format(_p)
        print("PASS contract verified verbatim against {}".format(_schema))

    print("PASS score_validity self-tests "
          "(3 garbage waves REJECTED, random-head signature DIAGNOSED, "
          "FU-108 wave ACCEPTED, extraction failure SEPARATED, "
          "counts path AGREES with rows path)")
