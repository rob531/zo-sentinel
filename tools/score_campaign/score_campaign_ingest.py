#!/usr/bin/env python3
"""score_campaign_ingest.py -- land a scoring wave's predictions into Fly PG.

Given the pod's preds.jsonl(.gz) and a DSN, this:
  1. UPSERTS the 7 axis rows per server into `mcp_llm_axis_scores`
     (model_version v3.0_40974559, adapter_sha256 bf842f54..) -- delete-then-insert
     keyed on (model_version, server_id), the idempotent shape weekly_rescore uses.
  2. Computes `mcp_server_registry.risk_tier` via gate_rule_v1 and writes it
     (+ last_assessed).

gate_rule_v1 (THE canonical decision rule -- NOT a composite, NOT _compute_tier):
  * escalation first: escalation_gate(overall_risk probs) -- p_crit >= 0.40 -> a
    CRITICAL escalation; else p_crit + p_high >= 0.30 -> a REVIEW escalation.
  * tier:
      - escalated CRITICAL                 -> CRITICAL
      - else                               -> argmax(overall_risk) label (LOW|MEDIUM|HIGH|CRITICAL)
      - REVIEW is a QUEUE FLAG only: it is stored (escalated / escalated_to) but
        does NOT change the stored tier.
  * then trust_gate cap (trust_gating_override.trust_gate): a verified / official
    publisher wearing HIGH or CRITICAL is capped to MEDIUM (anti trade-libel).

The escalation cutoffs and the trust cap are REUSED from the proven modules
(tools/rescore/calibration.escalation_gate, trust_gating_override.trust_gate) --
never re-derived here.

IDEMPOTENT + RESUMABLE: delete-then-insert per (model_version, server_id) and a
deterministic tier mean re-running a wave produces identical rows and identical
tiers. Batches commit as they go, so a re-run after an interruption simply
re-lands the same servers harmlessly.

DEFAULT DRY-RUN. Pass --apply to write. The DSN is a parameter; nothing is
hard-wired to prod.

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python score_campaign_ingest.py --preds results/preds.jsonl.gz \
        --dsn-file _dsn.txt --apply
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "rescore"))   # calibration, score_validity
sys.path.insert(0, str(REPO_ROOT))                 # trust_gating_override
from score_campaign_db import DB  # noqa: E402
from calibration import escalation_gate, RULE_V1  # noqa: E402  (stdlib-only, proven)
from trust_gating_override import trust_gate  # noqa: E402  (stdlib-only, proven)

MODEL_VERSION = "v3.0_40974559"
ADAPTER_SHA_PIN = "bf842f5450347c19ac195ef32a7298a1579566a7b9b454a5381fda333e7198e3"
AXES = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface"]
BATCH_SERVERS = 400

# axis insert column order (matches mcp_llm_axis_scores)
AXIS_COLS = ("server_id", "axis_name", "label", "label_index", "probs", "p_top",
             "p_critical", "p_danger", "escalated", "escalated_to",
             "decision_rule_version", "model_version", "adapter_sha256", "scored_at")


def gate_rule_v1_tier(overall_label: str, escalated: bool, escalated_to) -> str:
    """CRITICAL-escalation override -> else argmax(overall_risk) label.
    REVIEW never reaches here as a tier -- it is a queue flag, handled by the caller
    leaving escalated_to == 'REVIEW' on the axis row without changing the tier."""
    if escalated and escalated_to == "CRITICAL":
        return "CRITICAL"
    return (overall_label or "").upper()


def _p_danger(axis: str, probs) -> float | None:
    """The 'danger-class' probability weekly_rescore records for two axes:
    maintainer_trust -> SUSPICIOUS (idx 4), network_egress -> ARBITRARY (idx 3)."""
    if axis == "maintainer_trust" and len(probs) > 4:
        return probs[4]
    if axis == "network_egress" and len(probs) > 3:
        return probs[3]
    return None


def iter_preds(path: Path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except Exception:
                continue


def load_registry_identity(db: DB, sids):
    """server_id -> (name, url) for trust_gate. Chunked to respect sqlite's limit."""
    out = {}
    CHUNK = 900
    sids = list(sids)
    for i in range(0, len(sids), CHUNK):
        chunk = sids[i:i + CHUNK]
        ph = ", ".join("%s" for _ in chunk)
        db.execute(f"SELECT server_id, name, COALESCE(url,'') FROM mcp_server_registry "
                   f"WHERE server_id IN ({ph})", tuple(chunk))
        for sid, name, url in db.fetchall():
            out[sid] = (name, url)
    return out


def build_rows_for_server(p, scored_at):
    """Return (axis_rows, tier_inputs) for one parsed pred record, or (None, None)
    if it carries no usable overall_risk axis."""
    sid = p.get("server_id") or (p.get("metadata") or {}).get("server_id")
    if not sid:
        return None, None
    pl = p.get("axis_pred_label", {}) or {}
    pi = p.get("axis_pred_int", {}) or {}
    pr = p.get("axis_probs", {}) or {}
    mp = p.get("axis_max_prob", {}) or {}

    esc, esc_to, pcrit = escalation_gate(pr.get("overall_risk", [0, 0, 0, 0]))

    rows = []
    for a in AXES:
        li = pi.get(a)
        if li == -1:
            continue
        lbl = pl.get(a)
        if lbl is None and li is None:
            continue
        pv = pr.get(a, []) or []
        ptop = mp.get(a)
        if ptop is None and pv:
            try:
                ptop = max(pv)
            except (TypeError, ValueError):
                ptop = None
        rows.append((
            sid, a, lbl, li, json.dumps(pv), ptop,
            pcrit if a == "overall_risk" else None,
            _p_danger(a, pv),
            bool(esc) if a == "overall_risk" else False,
            esc_to if a == "overall_risk" else None,
            RULE_V1, MODEL_VERSION, ADAPTER_SHA_PIN, scored_at,
        ))
    if not any(r[1] == "overall_risk" for r in rows):
        return None, None  # cannot assign a tier without overall_risk
    tier_inputs = {
        "overall_label": pl.get("overall_risk"),
        "maintainer_trust": pl.get("maintainer_trust"),
        "escalated": bool(esc),
        "escalated_to": esc_to,
    }
    return rows, tier_inputs


def compute_tier(tier_inputs, name, url):
    """gate_rule_v1 (argmax / CRITICAL-escalation) then trust_gate cap."""
    base = gate_rule_v1_tier(tier_inputs["overall_label"],
                             tier_inputs["escalated"], tier_inputs["escalated_to"])
    override = trust_gate(url, name, {"overall_risk": base,
                                      "maintainer_trust": tier_inputs.get("maintainer_trust")})
    return base, override["published_overall_risk"], override


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a scoring wave into Fly PG (idempotent, dry-run by default).")
    ap.add_argument("--preds", required=True, help="preds.jsonl(.gz) from the pod")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dsn", help="DSN inline (sqlite:///... or postgresql://...)")
    g.add_argument("--dsn-file", help="file containing the DSN")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--apply", action="store_true", help="actually write (default dry-run)")
    a = ap.parse_args()

    dsn = a.dsn if a.dsn else Path(a.dsn_file).read_text(encoding="utf-8").strip()
    preds_path = Path(a.preds)
    if not preds_path.exists():
        raise SystemExit(f"FATAL: preds file not found: {preds_path}")

    scored_at = datetime.datetime.utcnow().isoformat(timespec="seconds")
    db = DB.connect(dsn, host=a.host, port=a.port)

    # Parse preds -> per-server axis rows + tier inputs (dedup by server_id).
    parsed, seen = [], set()
    n_records = n_parsed = 0
    for p in iter_preds(preds_path):
        n_records += 1
        if p.get("status") not in (None, "parsed"):
            continue
        n_parsed += 1
        rows, ti = build_rows_for_server(p, scored_at)
        if rows is None:
            continue
        sid = rows[0][0]
        if sid in seen:
            continue
        seen.add(sid)
        parsed.append((sid, rows, ti))
    print(f"[ingest] preds: {n_records} records, {n_parsed} parsed, "
          f"{len(parsed)} servers with a scorable overall_risk")
    if not parsed:
        print("[ingest] nothing to ingest.")
        db.close()
        return 0

    ident = load_registry_identity(db, [sid for sid, _, _ in parsed])

    stats = {"servers": 0, "axis_rows": 0, "capped": 0, "review_queued": 0}
    tier_dist = {}

    def flush(batch):
        if not batch:
            return
        sids = [sid for sid, _, _ in batch]
        all_rows = [r for _, rows, _ in batch for r in rows]
        if a.apply:
            ph = ", ".join("%s" for _ in sids)
            db.execute(f"DELETE FROM mcp_llm_axis_scores WHERE model_version = %s "
                       f"AND server_id IN ({ph})", tuple([MODEL_VERSION] + sids))
            db.insert_rows("mcp_llm_axis_scores", AXIS_COLS, all_rows)
        stats["axis_rows"] += len(all_rows)
        for sid, rows, ti in batch:
            name, url = ident.get(sid, (None, None))
            _base, tier, override = compute_tier(ti, name, url)
            tier_dist[tier] = tier_dist.get(tier, 0) + 1
            if override["capped"]:
                stats["capped"] += 1
            if ti["escalated"] and ti["escalated_to"] == "REVIEW":
                stats["review_queued"] += 1
            if a.apply:
                db.execute("UPDATE mcp_server_registry SET risk_tier = %s, last_assessed = %s "
                           "WHERE server_id = %s", (tier, scored_at, sid))
            stats["servers"] += 1
        if a.apply:
            db.commit()

    batch = []
    for item in parsed:
        batch.append(item)
        if len(batch) >= BATCH_SERVERS:
            flush(batch)
            batch = []
    flush(batch)

    mode = "APPLIED" if a.apply else "DRY-RUN (no writes)"
    print(f"[ingest] {mode}: servers={stats['servers']} axis_rows={stats['axis_rows']}")
    print(f"[ingest] tier distribution: {dict(sorted(tier_dist.items()))}")
    print(f"[ingest] trust-capped to MEDIUM: {stats['capped']} | "
          f"REVIEW-queued (tier unchanged): {stats['review_queued']}")
    if not a.apply:
        print("[ingest] re-run with --apply to write.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
