from collections import Counter
from datetime import datetime, timezone
import argparse

from app.db import get_session
from app.models import McpServerRegistry


_UNASSESSED = "UNASSESSED"
_EPOCH = "1970-01-01T00:00:00Z"


def _value(row, name, position=None):
    if isinstance(row, dict):
        return row.get(name)
    if position is not None:
        try:
            return row[position]
        except (IndexError, KeyError, TypeError):
            pass
    return getattr(row, name, None)


def _rows_from_source(source):
    if source is None:
        return []

    query = getattr(source, "query", None)
    if callable(query):
        return list(query(McpServerRegistry).all())

    execute = getattr(source, "execute", None)
    if callable(execute) and hasattr(McpServerRegistry, "__table__"):
        result = execute(McpServerRegistry.__table__.select())
        rows = []
        for row in result:
            if hasattr(row, "_mapping"):
                rows.append(dict(row._mapping))
            elif isinstance(row, dict):
                rows.append(dict(row))
            else:
                rows.append(row)
        return rows

    return list(source)


def _normalize_tier(value):
    if value is None:
        return _UNASSESSED
    text = str(value).strip()
    if not text:
        return _UNASSESSED
    return text.upper()


def _normalize_verdict(value):
    if value is None:
        return _UNASSESSED
    text = str(value).strip()
    if not text:
        return _UNASSESSED
    return text.upper()


def _score(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value):
    if value is None:
        return _EPOCH
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_report(conn_or_rows):
    rows = _rows_from_source(conn_or_rows)
    tier_counts = Counter()
    verdict_counts = Counter()
    tier_scores = {}
    latest_assessment = None
    stale_unassessed = 0

    for row in rows:
        tier = _normalize_tier(_value(row, "risk_tier", 1))
        verdict = _normalize_verdict(_value(row, "verdict", 2))
        score = _score(_value(row, "trust_score", 3))
        assessed = _datetime(_value(row, "last_assessed", 4))

        tier_counts[tier] += 1
        verdict_counts[verdict] += 1
        tier_scores.setdefault(tier, [])
        if score is not None:
            tier_scores[tier].append(score)

        if tier == _UNASSESSED:
            stale_unassessed += 1
        if assessed is not None and (latest_assessment is None or assessed > latest_assessment):
            latest_assessment = assessed

    total_servers = len(rows)
    tiers = []
    cutoffs = {}

    for tier in sorted(tier_counts, key=lambda item: (-tier_counts[item], item)):
        count = tier_counts[tier]
        scores = tier_scores[tier]
        average = round(sum(scores) / len(scores), 2) if scores else None
        pct = round((count / total_servers) * 100, 2) if total_servers else 0.0

        tiers.append(
            {
                "tier": tier,
                "count": count,
                "pct": pct,
                "avg_trust_score": average,
            }
        )
        cutoffs[tier] = {
            "min_trust": min(scores) if scores else None,
            "max_trust": max(scores) if scores else None,
        }

    verdicts = [
        {"verdict": verdict, "count": verdict_counts[verdict]}
        for verdict in sorted(
            verdict_counts,
            key=lambda item: (-verdict_counts[item], item),
        )
    ]

    return {
        "generated_at": _iso(latest_assessment),
        "total_servers": total_servers,
        "tiers": tiers,
        "verdicts": verdicts,
        "stale_unassessed": stale_unassessed,
        "cutoffs": cutoffs,
    }


def _display_score(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def render_report(summary):
    lines = [
        "# Fleet Risk-Tier Distribution",
        "",
        f"Generated at: {summary.get('generated_at', _EPOCH)}",
        f"Total servers: {summary.get('total_servers', 0)}",
        "",
        "## Risk tiers",
        "",
        "| Tier | Count | Percentage | Average trust score |",
        "| --- | ---: | ---: | ---: |",
    ]

    tiers = summary.get("tiers", [])
    for tier in tiers:
        lines.append(
            "| {tier} | {count} | {pct:.2f}% | {avg} |".format(
                tier=tier.get("tier", ""),
                count=tier.get("count", 0),
                pct=tier.get("pct", 0.0),
                avg=_display_score(tier.get("avg_trust_score")),
            )
        )

    lines.extend(
        [
            "",
            "## Verdicts",
            "",
            "| Verdict | Count |",
            "| --- | ---: |",
        ]
    )
    for verdict in summary.get("verdicts", []):
        lines.append(
            "| {verdict} | {count} |".format(
                verdict=verdict.get("verdict", ""),
                count=verdict.get("count", 0),
            )
        )

    lines.extend(
        [
            "",
            f"Stale or unassessed servers: {summary.get('stale_unassessed', 0)}",
            "",
            "## Trust-score cutoffs",
            "",
            "| Tier | Minimum | Maximum |",
            "| --- | ---: | ---: |",
        ]
    )
    for tier in tiers:
        name = tier.get("tier", "")
        cutoff = summary.get("cutoffs", {}).get(name, {})
        lines.append(
            "| {tier} | {minimum} | {maximum} |".format(
                tier=name,
                minimum=_display_score(cutoff.get("min_trust")),
                maximum=_display_score(cutoff.get("max_trust")),
            )
        )

    return "\n".join(lines) + "\n"


def _session_rows():
    context = get_session()
    if hasattr(context, "__enter__"):
        with context as session:
            return build_report(session)

    session = next(context)
    try:
        return build_report(session)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
        close_context = getattr(context, "close", None)
        if callable(close_context):
            close_context()


def main():
    parser = argparse.ArgumentParser(
        description="Export the current fleet risk-tier distribution."
    )
    parser.add_argument("--out", help="write the report to this path")
    args = parser.parse_args()

    summary = _session_rows()
    report = render_report(summary)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as output:
            output.write(report)

    print(report, end="")


if __name__ == "__main__":
    fake_rows = [
        {
            "server_id": "server-1",
            "risk_tier": "CRITICAL",
            "verdict": "BLOCK",
            "trust_score": 10,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        {
            "server_id": "server-2",
            "risk_tier": "CRITICAL",
            "verdict": "BLOCK",
            "trust_score": 20,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        {
            "server_id": "server-3",
            "risk_tier": "LOW",
            "verdict": "ALLOW",
            "trust_score": 80,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        {
            "server_id": "server-4",
            "risk_tier": "LOW",
            "verdict": "ALLOW",
            "trust_score": 90,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        {
            "server_id": "server-5",
            "risk_tier": "LOW",
            "verdict": "ALLOW",
            "trust_score": None,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        {
            "server_id": "server-6",
            "risk_tier": None,
            "verdict": None,
            "trust_score": None,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
    ]

    first = build_report(fake_rows)
    second = build_report(fake_rows)
    first_tiers = {item["tier"]: item["count"] for item in first["tiers"]}

    assert first["total_servers"] == 6
    assert first_tiers.get("CRITICAL", 0) == 2
    assert first_tiers.get("HIGH", 0) == 0
    assert first_tiers.get("CRITICAL", 0) + first_tiers.get("HIGH", 0) == 2
    assert first_tiers.get("LOW", 0) == 3
    assert first_tiers.get("UNASSESSED", 0) == 1
    assert abs(sum(item["pct"] for item in first["tiers"]) - 100) <= 0.01
    report = render_report(first)
    assert isinstance(report, str)
    assert "CRITICAL" in report
    assert "LOW" in report
    assert "UNASSESSED" in report
    assert first == second
    assert report == render_report(second)

    print("PASS")
else:
    main()