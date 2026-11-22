"""app_scoring_consumer_decision_rule_version.py

Pure scoring-consumer helper for the 3-tier app surface: summarise which
decision-rule version and model version produced the current risk picture.

The caller supplies the rows (read from mcp_llm_axis_scores: server_id,
decision_rule_version, model_version, scored_at). This module is pure --
no DB writes, no network, no file I/O, no imports of protected modules.

CONTRACT:
  summarize_rule_versions(rows: list[dict]) -> dict
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

UTC = datetime.timezone.utc


def _norm_version(value: Any) -> Optional[str]:
    """Normalise a version string; empty/None -> None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_datetime(value: Any) -> Optional[datetime.datetime]:
    """Parse ISO-8601 (or datetime) into an aware UTC datetime; None if absent/unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt


def _newest_version(rows: List[Dict[str, Any]], key: str) -> Optional[str]:
    """Version attached to the row with the highest scored_at (rows lacking a
    scored_at or the version itself are ignored for this determination)."""
    best_version: Optional[str] = None
    best_dt: Optional[datetime.datetime] = None
    for row in rows:
        version = _norm_version(row.get(key))
        if version is None:
            continue
        scored_at = _as_datetime(row.get("scored_at"))
        if scored_at is None:
            continue
        if best_dt is None or scored_at > best_dt:
            best_dt = scored_at
            best_version = version
    return best_version


def summarize_rule_versions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise the decision-rule / model versions behind a set of score rows.

    Args:
        rows: list of dicts with keys 'server_id', 'decision_rule_version',
              'model_version' and optionally 'scored_at' (ISO-8601 str/datetime).

    Returns:
        {
            'decision_rule_version': str | None,   # authoritative (newest scored_at)
            'model_version': str | None,           # authoritative (newest scored_at)
            'distinct_rule_versions': int,
            'distinct_model_versions': int,
            'servers_by_rule_version': {rule_version: server_count},
            'mixed_versions': bool,
        }
    """
    rows = list(rows or [])

    rule_versions = set()
    model_versions = set()
    servers_by_rule: Dict[str, set] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        rule = _norm_version(row.get("decision_rule_version"))
        model = _norm_version(row.get("model_version"))
        if rule is not None:
            rule_versions.add(rule)
            server_id = row.get("server_id")
            key = str(server_id).strip() if server_id is not None else ""
            servers_by_rule.setdefault(rule, set()).add(key if key else f"__anon_{id(row)}")
        if model is not None:
            model_versions.add(model)

    summary: Dict[str, Any] = {
        "decision_rule_version": _newest_version(rows, "decision_rule_version"),
        "model_version": _newest_version(rows, "model_version"),
        "distinct_rule_versions": len(rule_versions),
        "distinct_model_versions": len(model_versions),
        "servers_by_rule_version": {k: len(v) for k, v in sorted(servers_by_rule.items())},
        "mixed_versions": len(rule_versions) > 1 or len(model_versions) > 1,
    }
    return summary


if __name__ == "__main__":
    # (a) empty list -> all-None/0 defaults
    empty = summarize_rule_versions([])
    assert empty["decision_rule_version"] is None, empty
    assert empty["model_version"] is None, empty
    assert empty["distinct_rule_versions"] == 0, empty
    assert empty["distinct_model_versions"] == 0, empty
    assert empty["servers_by_rule_version"] == {}, empty
    assert empty["mixed_versions"] is False, empty

    # (b) three rows, one rule version, one model version
    same = summarize_rule_versions(
        [
            {"server_id": "s1", "decision_rule_version": "rule_9", "model_version": "ft_v3",
             "scored_at": "2026-11-01T10:00:00Z"},
            {"server_id": "s2", "decision_rule_version": "rule_9", "model_version": "ft_v3",
             "scored_at": "2026-11-02T10:00:00Z"},
            {"server_id": "s3", "decision_rule_version": "rule_9", "model_version": "ft_v3",
             "scored_at": "2026-11-03T10:00:00Z"},
        ]
    )
    assert same["decision_rule_version"] == "rule_9", same
    assert same["model_version"] == "ft_v3", same
    assert same["distinct_rule_versions"] == 1, same
    assert same["distinct_model_versions"] == 1, same
    assert same["servers_by_rule_version"] == {"rule_9": 3}, same
    assert same["mixed_versions"] is False, same

    # (c) two rule versions -> newest scored_at wins, mixed_versions True
    mixed = summarize_rule_versions(
        [
            {"server_id": "s1", "decision_rule_version": "rule_8", "model_version": "ft_v2",
             "scored_at": "2026-10-01T08:00:00Z"},
            {"server_id": "s2", "decision_rule_version": "rule_9", "model_version": "ft_v3",
             "scored_at": "2026-11-05T08:00:00Z"},
            {"server_id": "s3", "decision_rule_version": "rule_8", "model_version": "ft_v2",
             "scored_at": "2026-10-20T08:00:00Z"},
        ]
    )
    assert mixed["decision_rule_version"] == "rule_9", mixed
    assert mixed["model_version"] == "ft_v3", mixed
    assert mixed["distinct_rule_versions"] == 2, mixed
    assert mixed["servers_by_rule_version"] == {"rule_8": 2, "rule_9": 1}, mixed
    assert mixed["mixed_versions"] is True, mixed

    # (d) rows lacking scored_at are ignored for authority but still counted
    partial = summarize_rule_versions(
        [
            {"server_id": "s1", "decision_rule_version": "rule_8", "model_version": "ft_v2"},
            {"server_id": "s2", "decision_rule_version": "rule_9", "model_version": "ft_v3",
             "scored_at": "2026-11-04T12:00:00Z"},
        ]
    )
    assert partial["decision_rule_version"] == "rule_9", partial
    assert partial["distinct_rule_versions"] == 2, partial
    assert partial["mixed_versions"] is True, partial

    # (e) None versions tolerated
    nones = summarize_rule_versions(
        [{"server_id": "s1", "decision_rule_version": None, "model_version": None}]
    )
    assert nones["decision_rule_version"] is None, nones
    assert nones["distinct_rule_versions"] == 0, nones
    assert nones["servers_by_rule_version"] == {}, nones
    assert nones["mixed_versions"] is False, nones

    print("PASS")
