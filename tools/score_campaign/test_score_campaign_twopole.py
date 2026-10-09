#!/usr/bin/env python3
"""test_score_campaign_twopole.py -- the hermetic sqlite gate for the Fly-PG
scoring campaign tools. NO Postgres, NO proxy, NO network, NO GPU.

Two poles, run against a throwaway sqlite DB seeded in-process and asserted on:

  (a) EXPORT picks ONLY never-scored distinct-URL rows and emits the
      DESCRIPTION-ONLY prompt -- asserts no tool-manifest / fingerprint /
      provenance block (the PR #85 train/serve-skew guard).
  (b) INGEST -> gate_rule_v1 maps a seeded CRITICAL-escalation row to CRITICAL,
      a HIGH overall_risk to HIGH, an official-publisher HIGH to MEDIUM (trust
      cap), and argmax for the rest.
  (c) RE-INGEST is idempotent: no duplicate axis rows, stable tiers.

Run:  python test_score_campaign_twopole.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
EXPORT = HERE / "score_campaign_export.py"
INGEST = HERE / "score_campaign_ingest.py"

SCHEMA = """
CREATE TABLE mcp_server_registry (
    server_id TEXT PRIMARY KEY,
    name TEXT, registry_source TEXT, url TEXT, description TEXT,
    risk_tier TEXT, last_assessed TEXT, verdict TEXT
);
CREATE TABLE mcp_llm_axis_scores (
    server_id TEXT, axis_name TEXT, label TEXT, label_index INTEGER,
    probs TEXT, p_top REAL, p_critical REAL, p_danger REAL,
    escalated INTEGER, escalated_to TEXT, decision_rule_version TEXT,
    model_version TEXT, adapter_sha256 TEXT, scored_at TEXT
);
"""

LONG = "A perfectly ordinary MCP server description that is well over twenty chars."
MV = "v3.0_40974559"

FORBIDDEN = ("fingerprint", "provenance", "tool manifest", "tool_manifest",
             "manifest", "sha256", "tools_json", "capability_fingerprint")


def seed(con: sqlite3.Connection):
    reg = [
        # ---- export pole: should be SELECTED ----
        ("s_new1", "alpha", "remote", "https://a.example/mcp", LONG, None, None, None),
        ("s_new2", "beta", "remote", "https://b.example/mcp", LONG, "unassessed", None, None),
        # same URL as s_new2 -> NOT a distinct-URL rep (one wins)
        ("s_new3", "beta2", "remote", "https://b.example/mcp", LONG, None, None, None),
        # ---- export pole: should be EXCLUDED ----
        ("s_scored", "gamma", "remote", "https://c.example/mcp", LONG, None, None, None),   # has a score
        ("s_scored_sib", "gamma2", "remote", "https://c.example/mcp", LONG, None, None, None),  # URL already scored
        ("s_short", "delta", "remote", "https://d.example/mcp", "too short", None, None, None),  # desc<=20
        ("s_assessed", "eps", "remote", "https://e.example/mcp", LONG, "HIGH", None, None),  # already tiered
        # ---- ingest pole: registry identities for trust_gate (short desc keeps
        #      them OUT of the export backlog; ingest only reads server_id/name/url) ----
        ("s_crit", "crit", "remote", "https://evil.example/mcp", "x", None, None, None),
        ("s_high", "high", "remote", "https://plain.example/mcp", "x", None, None, None),
        ("s_official", "stripe", "remote", "https://github.com/stripe/mcp-server", "x", None, None, None),
        ("s_low", "low", "remote", "https://plain2.example/mcp", "x", None, None, None),
    ]
    con.executemany(
        "INSERT INTO mcp_server_registry "
        "(server_id,name,registry_source,url,description,risk_tier,last_assessed,verdict) "
        "VALUES (?,?,?,?,?,?,?,?)", reg)
    # s_scored already has an overall_risk axis score at MV -> excluded from backlog
    con.execute(
        "INSERT INTO mcp_llm_axis_scores "
        "(server_id,axis_name,label,label_index,probs,p_top,model_version,scored_at) "
        "VALUES ('s_scored','overall_risk','LOW',0,'[1,0,0,0]',1.0,?, '2026-01-01')", (MV,))
    con.commit()


def preds_records():
    def rec(sid, ov_label, ov_int, ov_probs):
        return {
            "server_id": sid, "status": "parsed",
            "axis_pred_label": {"overall_risk": ov_label, "auth_strength": "WEAK",
                                "maintainer_trust": "UNKNOWN_AUTHOR"},
            "axis_pred_int": {"overall_risk": ov_int, "auth_strength": 2,
                              "maintainer_trust": 3},
            "axis_probs": {"overall_risk": ov_probs,
                           "auth_strength": [0.1, 0.2, 0.6, 0.1],
                           "maintainer_trust": [0.1, 0.1, 0.1, 0.6, 0.1]},
            "axis_max_prob": {"overall_risk": max(ov_probs), "auth_strength": 0.6,
                              "maintainer_trust": 0.6},
        }
    return [
        # argmax LOW but p_crit=0.42 >= 0.40 -> escalation override to CRITICAL
        rec("s_crit", "LOW", 0, [0.45, 0.05, 0.08, 0.42]),
        # argmax HIGH; p_crit+p_high=0.7 -> REVIEW (queue flag), tier stays HIGH
        rec("s_high", "HIGH", 2, [0.10, 0.20, 0.60, 0.10]),
        # same as s_high but official publisher -> trust cap to MEDIUM
        rec("s_official", "HIGH", 2, [0.10, 0.20, 0.60, 0.10]),
        # argmax LOW, no escalation -> LOW
        rec("s_low", "LOW", 0, [0.70, 0.20, 0.08, 0.02]),
    ]


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"command failed rc={r.returncode}")
    return r


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="score_campaign_test_"))
    db_path = tmp / "campaign.db"
    dsn = f"sqlite:///{db_path.as_posix()}"
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    seed(con)
    con.close()

    print("\n=== POLE (a): EXPORT selects never-scored distinct-URL + description-only prompt ===")
    out = tmp / "wave.jsonl"
    run([PY, str(EXPORT), "--dsn", dsn, "--out", str(out)])
    lines = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    got_ids = sorted(r["metadata"]["server_id"] for r in lines)
    assert got_ids == ["s_new1", "s_new2"], f"export selection wrong: {got_ids}"
    for r in lines:
        msgs = r["messages"]
        assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user", "message roles"
        user = msgs[1]["content"]
        assert user.startswith("MCP SERVER UNDER REVIEW:"), "prompt header missing"
        assert "description:" in user, "description-only body missing"
        assert "SIGNALS TO LABEL" in user, "7-axis rubric missing"
        low = user.lower()
        for bad in FORBIDDEN:
            assert bad not in low, f"train/serve skew: forbidden block '{bad}' in prompt"
    print(f"  PASS: exported exactly {got_ids}; prompt is description-only (no "
          f"{'/'.join(FORBIDDEN[:3])}.. block)")

    print("\n=== POLE (b): INGEST -> gate_rule_v1 tiers (escalation / argmax / trust cap) ===")
    preds = tmp / "preds.jsonl"
    preds.write_text("\n".join(json.dumps(p) for p in preds_records()) + "\n", encoding="utf-8")
    run([PY, str(INGEST), "--preds", str(preds), "--dsn", dsn, "--apply"])

    con = sqlite3.connect(db_path)
    tiers = dict(con.execute(
        "SELECT server_id, risk_tier FROM mcp_server_registry "
        "WHERE server_id IN ('s_crit','s_high','s_official','s_low')").fetchall())
    expected = {"s_crit": "CRITICAL", "s_high": "HIGH", "s_official": "MEDIUM", "s_low": "LOW"}
    assert tiers == expected, f"tiers wrong: {tiers} != {expected}"
    # escalation flags on the overall_risk axis row
    esc = dict((sid, (e, et)) for sid, e, et in con.execute(
        "SELECT server_id, escalated, escalated_to FROM mcp_llm_axis_scores "
        "WHERE axis_name='overall_risk' AND model_version=? "
        "AND server_id IN ('s_crit','s_high','s_official','s_low')", (MV,)).fetchall())
    assert esc["s_crit"] == (1, "CRITICAL"), esc
    assert esc["s_high"] == (1, "REVIEW"), esc
    assert esc["s_official"] == (1, "REVIEW"), esc
    assert esc["s_low"][0] == 0, esc
    print(f"  PASS: tiers {tiers}")
    print(f"  PASS: escalation flags {esc}")

    print("\n=== POLE (c): RE-INGEST is idempotent (no dup rows, stable tier) ===")
    n1 = con.execute("SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE model_version=? "
                     "AND server_id IN ('s_crit','s_high','s_official','s_low')", (MV,)).fetchone()[0]
    con.close()
    run([PY, str(INGEST), "--preds", str(preds), "--dsn", dsn, "--apply"])
    con = sqlite3.connect(db_path)
    n2 = con.execute("SELECT COUNT(*) FROM mcp_llm_axis_scores WHERE model_version=? "
                     "AND server_id IN ('s_crit','s_high','s_official','s_low')", (MV,)).fetchone()[0]
    tiers2 = dict(con.execute(
        "SELECT server_id, risk_tier FROM mcp_server_registry "
        "WHERE server_id IN ('s_crit','s_high','s_official','s_low')").fetchall())
    con.close()
    assert n1 == n2, f"duplicate rows on re-ingest: {n1} -> {n2}"
    assert tiers2 == expected, f"tiers drifted on re-ingest: {tiers2}"
    print(f"  PASS: axis rows stable at {n1} across two applies; tiers stable {tiers2}")

    print("\nALL POLES PASSED (hermetic sqlite).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
