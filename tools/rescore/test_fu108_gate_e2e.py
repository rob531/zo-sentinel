#!/usr/bin/env python3
"""End-to-end proof: patched gate against the REAL 7/26 preds + REAL sft schema.

Exercises exactly the code path ph_import now runs, plus the contract-drift
check against schemas/risk_axis_mapping_v1.json fetched from the SFT repo.
"""
import gzip, json, sys

sys.path.insert(0, r"D:\zo\_wt_fu108\tools\rescore")
from score_validity import (assert_importable, extract_axis_rows, verify_against_schema, ExtractionFailure,
                            CANONICAL_AXIS_ORDER)

PREDS = r"D:\zo\runs\weekly_rescore\20260726-014732\results\preds.jsonl.gz"
SCHEMA = r"D:\zo\Zocomputer Agents\_fu108\risk_axis_mapping_v1.json"
AXES = list(CANONICAL_AXIS_ORDER)

print("=" * 72)
print("1. CONTRACT DRIFT vs rob531/zomesh-sentinel-sft risk_axis_mapping_v1.json")
print("=" * 72)
problems = verify_against_schema(SCHEMA)
if problems:
    print("  DRIFT DETECTED:")
    for p in problems:
        print("    - " + p)
    sys.exit(1)
print("  OK - all 7 axes match the schema verbatim (labels, num_classes,")
print("       off_ladder_indices).")

print()
print("=" * 72)
print("2. GATE against the REAL preds (run 20260726-014732)")
print("=" * 72)
recs = []
with gzip.open(PREDS, "rt", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            recs.append(json.loads(line))
parsed = sum(1 for d in recs if d.get("status") == "parsed")
rows = extract_axis_rows(recs, AXES)
print("  records=%d parsed=%d -> gradeable rows=%d" % (len(recs), parsed, len(rows)))
assert len(rows) > 0, "extraction still broken"

try:
    report = assert_importable(rows, source_records=len(recs))
except ExtractionFailure as e:
    print("  EXTRACTION_FAILURE: %s" % e)
    sys.exit(1)
except SystemExit as e:
    print("  GATE REFUSED: %s" % e)
    sys.exit(1)

print("  GATE PASS")
print()
for a in report["axes"]:
    mark = " <-- DECLARED EXCEPTION" if a["verdict"] == "VALID_DECLARED" else ""
    print("    %-20s %-14s n=%-7d top=%-15s %5.1f%%  %d distinct  %.3f bits%s" % (
        a["axis"], a["verdict"], a["n"], a.get("top_label"),
        a.get("top_share", 0) * 100, a.get("distinct_labels", 0),
        a.get("entropy_bits", 0), mark))
print()
print("  importable=%s  random_head_signature=%s  n_collapsed=%d" % (
    report["importable"], report["random_head_signature"], report["n_collapsed"]))

print()
print("=" * 72)
print("3. WRITER PARITY (the FU-108 differential)")
print("=" * 72)
writer_rows = 0
seen = set()
for p in recs:
    if p.get("status") != "parsed":
        continue
    sid = p.get("server_id") or (p.get("metadata") or {}).get("server_id")
    if not sid or sid in seen:
        continue
    seen.add(sid)
    pi = p.get("axis_pred_int", {})
    for a in AXES:
        if pi.get(a) == -1:
            continue
        writer_rows += 1
print("  gate judged  : %d" % len(rows))
print("  writer writes: %d" % writer_rows)
print("  servers      : %d" % len(seen))
if writer_rows != len(rows):
    print("  MISMATCH - the differential would fire")
    sys.exit(1)
print("  OK - parity holds, differential will not fire")

print()
print("=" * 72)
print("4. REGRESSION: the 3 garbage waves must STILL be refused")
print("=" * 72)


def synth(axis, hist):
    return [{"axis_name": axis, "label": l} for l, n in hist.items() for _ in range(n)]


for name, bad in [
    ("2026-07-18 (100% HIGH)", synth("overall_risk", {"HIGH": 86049, "CRITICAL": 1})),
    ("2026-07-21 (100% CRITICAL)", synth("overall_risk", {"CRITICAL": 65045})),
    ("2026-07-24 (100% LOW)", synth("overall_risk", {"LOW": 125726, "CRITICAL": 5})),
    ("2026-07-24 multi-axis", synth("overall_risk", {"LOW": 125726, "CRITICAL": 5})
     + synth("auth_strength", {"WEAK": 125723, "UNKNOWN": 8})),
]:
    try:
        assert_importable(bad)
        print("  !!! FAIL - %s was ACCEPTED" % name)
        sys.exit(1)
    except SystemExit as e:
        sig = "RANDOM-HEAD SIGNATURE" in str(e)
        print("  REFUSED  %-30s %s" % (name, "(diagnosed as random heads)" if sig else ""))

print()
print("=" * 72)
print("ALL CHECKS PASS - the 7/26 wave is importable; garbage still refused.")
print("=" * 72)
