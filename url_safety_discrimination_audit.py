#!/usr/bin/env python3
"""
Signal discrimination audit.

Reports, for every signal in mcp_signal_scores, whether it actually tells
servers apart -- and whether it is being written more often than it can
possibly change.

WHY THIS FILE WAS REWRITTEN (2026-09-15)
----------------------------------------
The previous version could not detect the condition it was built for. It ran:

    SELECT score_value, COUNT(*) FROM mcp_signal_scores
    WHERE signal_type='url_safety' GROUP BY score_value
    JOIN ... ON s1.mcp_request_id = s2.mcp_request_id

against a table whose columns are `score` and `signal_name`, with no
`mcp_request_id` at all -- and then read `query_result['data']` from a service
that returns `rows`. Every one of those queries answers HTTP 400 (measured:
binder, parser and unknown-table errors all do). The request was wrapped in
`except RequestException: return None`, and analyze_signal_quality(None)
returned {'distinct_scores': 0, 'error': 'No data returned'}.

Zero distinct scores READS AS "no data" rather than "total uniformity", so the
audit reported clean unconditionally. At least eight directives aimed at
url_safety discrimination closed .done against it while the signal sat at 4
distinct scores over 2.4M rows.

So this version fails CLOSED. It reuses the query layer in
enrichment/enrichment_preflight.py, which raises on any response that is not a
`rows` payload, and asserts every column it reads exists before reading it. A
check that cannot run exits non-zero; it never reports clean by default.

Exit codes: 0 all signals discriminate, 1 at least one is degenerate,
2 the audit could not be evaluated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'enrichment'))

from enrichment_preflight import PreflightError, assert_columns, query  # noqa: E402

SIGNAL_TABLE = 'mcp_signal_scores'

# A signal with two or fewer distinct values is a constant wearing a score
# column. Five or fewer, or one value covering most of the rows, is weak.
DEGENERATE_DISTINCT = 2
WEAK_DISTINCT = 5
WEAK_TOP_SHARE = 0.80
# Rows per server above this means the writer is appending rather than
# replacing -- the amplification nobody was watching.
MAX_ROWS_PER_SERVER = 3.0


def audit_signals(min_rows: int = 100) -> list[dict]:
    assert_columns(SIGNAL_TABLE, ['server_id', 'signal_name', 'score', 'scored_at'])

    rows = query(f"""
        SELECT signal_name,
               COUNT(*)                  AS rows_total,
               COUNT(DISTINCT server_id) AS servers,
               COUNT(DISTINCT score)     AS distinct_scores,
               MIN(score)                AS min_score,
               MAX(score)                AS max_score,
               MAX(scored_at)            AS last_written
        FROM {SIGNAL_TABLE}
        WHERE signal_name IS NOT NULL
        GROUP BY signal_name
        ORDER BY rows_total DESC
    """)

    findings = []
    for r in rows:
        if r['rows_total'] < min_rows:
            continue
        name = r['signal_name']

        top = query(f"""
            SELECT COUNT(*) AS n FROM {SIGNAL_TABLE}
            WHERE signal_name = '{name.replace("'", "''")}'
              AND score = (
                  SELECT score FROM {SIGNAL_TABLE}
                  WHERE signal_name = '{name.replace("'", "''")}'
                  GROUP BY score ORDER BY COUNT(*) DESC LIMIT 1
              )
        """)
        top_share = (top[0]['n'] / r['rows_total']) if r['rows_total'] else 0.0
        per_server = (r['rows_total'] / r['servers']) if r['servers'] else 0.0

        if r['distinct_scores'] <= DEGENERATE_DISTINCT:
            verdict = 'DEGENERATE'
        elif r['distinct_scores'] <= WEAK_DISTINCT or top_share >= WEAK_TOP_SHARE:
            verdict = 'WEAK'
        else:
            verdict = 'OK'

        findings.append({
            'signal_name': name,
            'verdict': verdict,
            'rows': r['rows_total'],
            'servers': r['servers'],
            'distinct_scores': r['distinct_scores'],
            'score_range': [r['min_score'], r['max_score']],
            'top_score_share': round(top_share, 4),
            'rows_per_server': round(per_server, 1),
            'amplified': per_server > MAX_ROWS_PER_SERVER,
            'last_written': str(r['last_written']),
        })
    return findings


def render(findings: list[dict]) -> str:
    out = ['=' * 78, 'Signal discrimination audit', f'Timestamp: {datetime.now().isoformat()}', '=' * 78, '']
    out.append(f"{'signal':<28}{'verdict':<12}{'distinct':>9}{'servers':>9}{'rows':>12}{'rows/srv':>10}")
    out.append('-' * 78)
    for f in findings:
        out.append(
            f"{f['signal_name']:<28}{f['verdict']:<12}{f['distinct_scores']:>9}"
            f"{f['servers']:>9}{f['rows']:>12,}{f['rows_per_server']:>10.1f}"
        )
    bad = [f for f in findings if f['verdict'] == 'DEGENERATE']
    weak = [f for f in findings if f['verdict'] == 'WEAK']
    amp = [f for f in findings if f['amplified']]

    out.append('')
    if bad:
        out.append(f"DEGENERATE ({len(bad)}) -- these carry no information:")
        for f in bad:
            out.append(
                f"  {f['signal_name']}: {f['distinct_scores']} distinct value(s) "
                f"across {f['rows']:,} rows, range {f['score_range']}"
            )
    if weak:
        out.append(f"WEAK ({len(weak)}) -- barely discriminating:")
        for f in weak:
            out.append(
                f"  {f['signal_name']}: {f['distinct_scores']} distinct, "
                f"top value covers {f['top_score_share']:.1%}"
            )
    if amp:
        out.append(f"AMPLIFIED ({len(amp)}) -- written more often than they can change:")
        for f in amp:
            out.append(f"  {f['signal_name']}: {f['rows_per_server']:.0f} rows per server")
    if not (bad or weak or amp):
        out.append('All signals discriminate and none are amplified.')
    return '\n'.join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description='Audit every signal for discrimination and write amplification.')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--min-rows', type=int, default=100)
    ap.add_argument('--fail-on-weak', action='store_true', help='exit 1 on WEAK as well as DEGENERATE')
    args = ap.parse_args()

    try:
        findings = audit_signals(min_rows=args.min_rows)
    except PreflightError as exc:
        # Fail closed. The old version turned this path into a clean report.
        print(f'AUDIT COULD NOT BE EVALUATED: {exc}', file=sys.stderr)
        return 2

    print(json.dumps(findings, indent=2) if args.json else render(findings))

    bad = [f for f in findings if f['verdict'] == 'DEGENERATE']
    if args.fail_on_weak:
        bad += [f for f in findings if f['verdict'] == 'WEAK']
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
