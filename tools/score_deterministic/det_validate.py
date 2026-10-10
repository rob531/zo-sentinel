#!/usr/bin/env python3
"""tools/score_deterministic/det_validate.py -- the TRUST CHECK.

Given a Postgres DSN, sample N (default 300) servers that the v3.0 student ALREADY
scored, re-score their descriptions with the deterministic backend, and report
PER-AXIS AGREEMENT between the deterministic labels and the stored student labels:

    exact%      -- deterministic label == stored student label
    neighbour%  -- for ordinal axes, within one ladder step (|idx_pred-idx_true|<=1)

This decides whether GLiDE/jev is good enough to REPLACE or COMPLEMENT the student.
It does NOT assert that it is -- it only reports the numbers. READ-ONLY (SELECT only;
no --apply, never writes).

Usage:
    fly proxy 15432:5432 -a mcplookup-db
    python tools/score_deterministic/det_validate.py --dsn-file <path> \
        [--backend glide|jev] [--n 300] [--student-version v3.0_40974559]

The agreement math (`agreement_report`) is pure and unit-tested without a DB.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

try:
    from .det_scorer import make_backend, score_server
    from .rubrics import AXES, AXES_BY_NAME, neighbour_ok
except ImportError:  # run directly
    from det_scorer import make_backend, score_server  # type: ignore
    from rubrics import AXES, AXES_BY_NAME, neighbour_ok  # type: ignore


def agreement_report(pairs: List[Tuple[str, str, str]]) -> Dict:
    """Pure agreement math. `pairs` is a list of (axis_name, predicted, stored).
    Returns per-axis {n, exact, exact_pct, neighbour, neighbour_pct, ordinal} plus
    an 'overall' roll-up. `neighbour` counts predictions within one ordinal step
    (exact matches are, by definition, also neighbours); for non-ordinal axes
    neighbour == exact."""
    per: Dict[str, Dict] = {}
    for axis, pred, stored in pairs:
        if pred is None or stored is None:
            continue
        a = per.setdefault(axis, {"n": 0, "exact": 0, "neighbour": 0})
        a["n"] += 1
        # exact: case-insensitive for all axes (labels are upper-case tokens, and
        # the deterministic scorer emits the class-set spelling verbatim).
        exact = str(pred).upper() == str(stored).upper()
        if exact:
            a["exact"] += 1
            a["neighbour"] += 1
            continue
        nb = neighbour_ok(axis, str(pred).upper(), str(stored).upper())
        if nb is True:
            a["neighbour"] += 1

    report = {"axes": {}, "overall": {"n": 0, "exact": 0, "neighbour": 0}}
    for axis_name in [a.name for a in AXES]:
        a = per.get(axis_name)
        ordinal = bool(AXES_BY_NAME[axis_name].ordinal)
        if not a or a["n"] == 0:
            report["axes"][axis_name] = {"n": 0, "exact": 0, "exact_pct": None,
                                         "neighbour": 0, "neighbour_pct": None,
                                         "ordinal": ordinal}
            continue
        n = a["n"]
        report["axes"][axis_name] = {
            "n": n, "exact": a["exact"],
            "exact_pct": round(100.0 * a["exact"] / n, 2),
            "neighbour": a["neighbour"],
            "neighbour_pct": round(100.0 * a["neighbour"] / n, 2),
            "ordinal": ordinal,
        }
        report["overall"]["n"] += n
        report["overall"]["exact"] += a["exact"]
        report["overall"]["neighbour"] += a["neighbour"]
    o = report["overall"]
    o["exact_pct"] = round(100.0 * o["exact"] / o["n"], 2) if o["n"] else None
    o["neighbour_pct"] = round(100.0 * o["neighbour"] / o["n"], 2) if o["n"] else None
    return report


def _parse_dsn(dsn: str) -> Tuple[str, str, str]:
    m = re.match(r"postgres(?:ql)?://([^:]+):([^@]+)@[^/]+/(\w+)", dsn)
    if not m:
        raise SystemExit("FATAL: unparseable DSN")
    return m.group(1), m.group(2), m.group(3)


def sample_scored_servers(cur, student_version: str, n: int) -> List[Dict]:
    """Pull N servers that HAVE student overall_risk scores, with their description
    and ALL stored axis labels for the student model_version. Deterministic sample
    (ORDER BY server_id) so re-runs are reproducible."""
    cur.execute(
        """
        select r.server_id, r.name, r.url, r.registry_source, r.description
        from mcp_server_registry r
        where r.description is not null and r.description <> ''
          and exists (
            select 1 from mcp_llm_axis_scores s
            where s.server_id = r.server_id
              and s.axis_name = 'overall_risk'
              and s.model_version = %s)
        order by r.server_id
        limit %s
        """,
        (student_version, n),
    )
    servers = []
    for sid, name, url, source, desc in cur.fetchall():
        cur.execute(
            """select axis_name, label from mcp_llm_axis_scores
               where server_id = %s and model_version = %s""",
            (sid, student_version),
        )
        stored = {ax: lbl for ax, lbl in cur.fetchall()}
        servers.append({"server_id": sid, "name": name, "url": url,
                        "source": source, "description": desc, "_stored": stored})
    return servers


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic-vs-student agreement (read-only).")
    ap.add_argument("--dsn-file", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=15432)
    ap.add_argument("--backend", choices=("glide", "jev"), default="glide")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--student-version", default="v3.0_40974559")
    ap.add_argument("--api-key-env", default=None)
    ap.add_argument("--json-out", default=None, help="also write the report JSON here")
    a = ap.parse_args(argv)

    import psycopg2

    user, pw, db = _parse_dsn(open(a.dsn_file).read().strip())
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=db, user=user, password=pw)
    cur = conn.cursor()

    servers = sample_scored_servers(cur, a.student_version, a.n)
    print(f"[validate] sampled {len(servers)} already-scored servers "
          f"(student={a.student_version}, backend={a.backend})")
    if not servers:
        print("FATAL: no student-scored servers with descriptions to validate against")
        return 1

    key_env = a.api_key_env or ("FASTINO_API_KEY" if a.backend == "glide" else "JEV_API_KEY")
    backend = make_backend(a.backend, api_key=os.environ.get(key_env, ""))

    pairs: List[Tuple[str, str, str]] = []
    errors = 0
    for i, srv in enumerate(servers, 1):
        try:
            rows = score_server(srv, backend)
        except Exception as e:  # keep going; a transport blip must not void the run
            errors += 1
            if errors <= 5:
                print(f"[validate] score error on {srv['server_id']}: {e}")
            continue
        pred = {r["axis_name"]: r["label"] for r in rows}
        for ax, stored_label in srv["_stored"].items():
            if ax in pred and stored_label is not None:
                pairs.append((ax, pred[ax], stored_label))
        if i % 50 == 0:
            print(f"[validate] scored {i}/{len(servers)}")

    report = agreement_report(pairs)
    report["_meta"] = {
        "backend": a.backend, "student_version": a.student_version,
        "sampled": len(servers), "score_errors": errors,
        "generated_at": datetime.datetime.utcnow().isoformat(),
    }
    print(json.dumps(report, indent=2))
    print("\n[validate] NOTE: this reports agreement numbers only. It does NOT assert "
          "GLiDE/jev is good enough to replace the student -- the operator reads the "
          "exact% / neighbour% and decides.")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"[validate] wrote {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
