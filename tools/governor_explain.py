#!/usr/bin/env python3
"""governor_explain.py -- dump the governor's per-artifact agreement so we can
see WHY each false-promote happens, instead of patching blind.

For every build_artifact the governor would grade, prints the ingestor verdict,
the resolved path + whether it exists on disk, gate_8's verdict, and the RAW
gate_8 `gate_checks` rows for that basename. A disagreement where the file
EXISTS and gate_8 shows BOTH pass and fail rows == the sticky-fail bug
(verdict_for treats any historical fail as fail, ignoring later passes).

Read-only: hits write_service /query (HTTP) for artifacts and opens
gate_errors.db read-only. Safe to run anytime.

Usage (on ZoComputer):
    PYTHONPATH=/home/workspace/zo_sentinel python3 /home/workspace/zo_sentinel/tools/governor_explain.py
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/workspace/zo_sentinel")

from zo_sentinel.ingestor.ingestor import ArtifactIngestor          # noqa: E402
from zo_sentinel.ingestor.governor import DuckDBGate8Source         # noqa: E402
from zo_sentinel.ingestor.model import BuildArtifact                # noqa: E402


def _gate_rows(db_path: str, base: str):
    try:
        import duckdb
        con = duckdb.connect(db_path, read_only=True)
        try:
            return con.execute(
                "SELECT status, check_name FROM gate_checks "
                "WHERE gate_name='gate_8_new_module' AND check_name LIKE ? LIMIT 12",
                [f"%{base}%"],
            ).fetchall()
        finally:
            con.close()
    except Exception as e:
        return [("<read-error>", str(e))]


def main() -> int:
    ing = ArtifactIngestor()
    gate8 = DuckDBGate8Source()
    wm = ing.store.get_watermark()
    rows = ing.store.read_build_artifacts(wm, 200)
    print(f"watermark={wm!r}  artifacts_read={len(rows)}  gate_errors.db={gate8.db_path}\n")

    seen: set[str] = set()
    comparable = agreed = false_promotes = too_strict = 0
    for row_id, content in rows:
        art = BuildArtifact.from_mesh_content(content, row_id=row_id)
        if art is None or art.dedup_key in seen:
            continue
        seen.add(art.dedup_key)

        v = ing.evaluate(art)
        g = gate8.verdict_for(art.file)
        if g is None:
            continue
        comparable += 1
        if v.ok == g:
            agreed += 1
            continue
        if v.ok and not g:
            false_promotes += 1
            kind = "FALSE-PROMOTE (ingestor PROMOTE / gate_8 FAIL)"
        else:
            too_strict += 1
            kind = "too-strict (ingestor QUARANTINE / gate_8 PASS)"

        path = ing._resolve(art)
        grows = _gate_rows(gate8.db_path, Path(art.file).name)
        statuses = {s for s, _ in grows}
        sticky = ("pass" in statuses) and bool({"fail", "error"} & statuses)
        print(f"{kind}")
        print(f"  file={art.file}")
        print(f"  ingestor: ok={v.ok} contract={v.contract} exists={path.exists()} path={path}")
        print(f"  gate_8:   verdict={g}  statuses={sorted(statuses)}  "
              f"{'<<< STICKY-FAIL (has pass AND fail)' if sticky else ''}")
        for s, cn in grows:
            print(f"      [{s}] {str(cn)[:90]}")
        print()

    print(f"SUMMARY comparable={comparable} agreed={agreed} "
          f"false_promotes={false_promotes} too_strict={too_strict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
