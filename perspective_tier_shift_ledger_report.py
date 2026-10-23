import json
from collections import Counter, defaultdict
from datetime import datetime, timezone


DEFAULT_TIER_ORDER = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_TRANT",
    "INSUFFICIENT",
]

_REQUIRED_EVENT_KEYS = {
    "perspective_id",
    "server_id",
    "change_type",
    "old_tier",
    "new_tier",
    "created_at",
}


def _parse_created_at(value):
    if not isinstance(value, str) or not value:
        raise ValueError("created_at must be a non-empty ISO-8601 string")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("created_at must be a valid ISO-8601 string") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_rows(rows):
    if not isinstance(rows, list):
        raise TypeError("rows must be a list of perspective event dictionaries")

    validated = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"rows[{index}] must be a dictionary")

        keys = set(row)
        if keys != _REQUIRED_EVENT_KEYS:
            missing = sorted(_REQUIRED_EVENT_KEYS - keys)
            extra = sorted(keys - _REQUIRED_EVENT_KEYS)
            details = []
            if missing:
                details.append(f"missing keys: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected keys: {', '.join(extra)}")
            raise ValueError(f"rows[{index}] has invalid keys ({'; '.join(details)})")

        for key in ("perspective_id", "server_id", "change_type", "created_at"):
            if not isinstance(row[key], str):
                raise TypeError(f"rows[{index}].{key} must be a string")

        for key in ("old_tier", "new_tier"):
            if row[key] is not None and not isinstance(row[key], str):
                raise TypeError(f"rows[{index}].{key} must be a string or None")

        _parse_created_at(row["created_at"])
        validated.append(row)

    return validated


def _transition_label(old_tier, new_tier):
    return f"{old_tier}->{new_tier}"


def _markdown_value(value):
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_tier_shift_ledger(rows, tier_order=None):
    """Build a deterministic ledger from saved Perspective events.

    The tier order is ordered from lowest risk to highest risk. Consequently,
    a transition to an earlier tier is an upgrade and a transition to a later
    tier is a downgrade.
    """
    validated = _validate_rows(rows)

    if tier_order is None:
        effective_tier_order = list(DEFAULT_TIER_ORDER)
    else:
        if not isinstance(tier_order, list):
            raise TypeError("tier_order must be a list of strings")
        if any(not isinstance(tier, str) for tier in tier_order):
            raise TypeError("tier_order must contain only strings")
        if len(set(tier_order)) != len(tier_order):
            raise ValueError("tier_order must not contain duplicate tiers")
        effective_tier_order = list(tier_order)

    rank = {tier: index for index, tier in enumerate(effective_tier_order)}

    perspective_events = defaultdict(list)
    global_transitions = Counter()
    unranked_servers = set()

    for row in validated:
        perspective_events[row["perspective_id"]].append(row)

        old_tier = row["old_tier"]
        new_tier = row["new_tier"]
        global_transitions[(old_tier, new_tier)] += 1

        if old_tier not in rank or new_tier not in rank:
            unranked_servers.add(row["server_id"])

    perspectives = []
    for perspective_id in sorted(perspective_events):
        events = perspective_events[perspective_id]
        by_transition = Counter()
        upgrades = 0
        downgrades = 0
        unchanged = 0
        servers_touched = set()

        for row in events:
            old_tier = row["old_tier"]
            new_tier = row["new_tier"]
            by_transition[_transition_label(old_tier, new_tier)] += 1
            servers_touched.add(row["server_id"])

            old_rank = rank.get(old_tier)
            new_rank = rank.get(new_tier)

            if old_rank is not None and new_rank is not None:
                if new_rank < old_rank:
                    upgrades += 1
                elif new_rank > old_rank:
                    downgrades += 1
                else:
                    unchanged += 1
            elif old_tier == new_tier:
                unchanged += 1

        perspectives.append(
            {
                "perspective_id": perspective_id,
                "event_count": len(events),
                "upgrades": upgrades,
                "downgrades": downgrades,
                "unchanged": unchanged,
                "by_transition": {
                    label: by_transition[label] for label in sorted(by_transition)
                },
                "servers_touched": sorted(servers_touched),
            }
        )

    transitions = [
        {
            "old_tier": old_tier,
            "new_tier": new_tier,
            "count": count,
        }
        for (old_tier, new_tier), count in sorted(
            global_transitions.items(),
            key=lambda item: (
                -item[1],
                "" if item[0][0] is None else item[0][0],
                "" if item[0][1] is None else item[0][1],
            ),
        )
    ]

    if validated:
        generated_at = max(
            validated,
            key=lambda row: _parse_created_at(row["created_at"]),
        )["created_at"]
    else:
        generated_at = ""

    return {
        "generated_at": generated_at,
        "event_count": len(validated),
        "perspectives": perspectives,
        "transitions": transitions,
        "unranked_servers": sorted(unranked_servers),
    }


def render_markdown(ledger):
    """Render a deterministic Markdown representation of a tier-shift ledger."""
    if not isinstance(ledger, dict):
        raise TypeError("ledger must be a dictionary")

    lines = [
        "# Tier Shift Ledger",
        "",
        f"- Generated at: {_markdown_value(ledger.get('generated_at', ''))}",
        f"- Events: {ledger.get('event_count', 0)}",
        "",
        "## Transitions",
        "",
        "| Old tier | New tier | Count |",
        "| --- | --- | ---: |",
    ]

    transitions = ledger.get("transitions", [])
    if transitions:
        for transition in transitions:
            lines.append(
                "| {old} | {new} | {count} |".format(
                    old=_markdown_value(transition.get("old_tier")),
                    new=_markdown_value(transition.get("new_tier")),
                    count=transition.get("count", 0),
                )
            )
    else:
        lines.append("| — | — | 0 |")

    lines.extend(
        [
            "",
            "## Perspectives",
            "",
            "| Perspective | Events | Upgrades | Downgrades | Unchanged | Servers touched |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )

    perspectives = ledger.get("perspectives", [])
    if perspectives:
        for perspective in perspectives:
            servers = ", ".join(
                _markdown_value(server)
                for server in perspective.get("servers_touched", [])
            )
            lines.append(
                "| {perspective} | {events} | {upgrades} | {downgrades} | "
                "{unchanged} | {servers} |".format(
                    perspective=_markdown_value(perspective.get("perspective_id")),
                    events=perspective.get("event_count", 0),
                    upgrades=perspective.get("upgrades", 0),
                    downgrades=perspective.get("downgrades", 0),
                    unchanged=perspective.get("unchanged", 0),
                    servers=servers,
                )
            )
    else:
        lines.append("| — | 0 | 0 | 0 | 0 | — |")

    unranked_servers = ledger.get("unranked_servers", [])
    lines.extend(["", "## Unranked Servers", ""])
    if unranked_servers:
        for server_id in unranked_servers:
            lines.append(f"- {_markdown_value(server_id)}")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def main():
    ledger = build_tier_shift_ledger([])
    print(json.dumps(ledger, ensure_ascii=False, sort_keys=False))


if __name__ == "__main__":
    _events = [
        {
            "perspective_id": "perspective-a",
            "server_id": "server-a",
            "change_type": "tier_changed",
            "old_tier": "TRUSTED_GENERAL",
            "new_tier": "ENTERPRISE_CONTROLLED",
            "created_at": "2025-01-01T00:00:00Z",
        },
        {
            "perspective_id": "perspective-a",
            "server_id": "server-b",
            "change_type": "tier_changed",
            "old_tier": "CAUTION_LIMITED",
            "new_tier": "ENTERPRISE_CONTROLLED",
            "created_at": "2025-01-01T00:01:00Z",
        },
        {
            "perspective_id": "perspective-b",
            "server_id": "server-c",
            "change_type": "tier_unchanged",
            "old_tier": "TRUSTED_GENERAL",
            "new_tier": "TRUSTED_GENERAL",
            "created_at": "2025-01-01T00:02:00Z",
        },
    ]

    _ledger = build_tier_shift_ledger(_events)

    assert _ledger["event_count"] == 3
    assert _ledger["perspectives"][0]["event_count"] == 2
    assert _ledger["perspectives"][0]["upgrades"] == 1
    assert _ledger["perspectives"][0]["downgrades"] == 1
    assert _ledger["perspectives"][1]["unchanged"] == 1
    assert _ledger["perspectives"][0]["by_transition"] == {
        "CAUTION_LIMITED->ENTERPRISE_CONTROLLED": 1,
        "TRUSTED_GENERAL->ENTERPRISE_CONTROLLED": 1,
    }

    _enterprise_controlled_count = sum(
        transition["count"]
        for transition in _ledger["transitions"]
        if transition["new_tier"] == "ENTERPRISE_CONTROLLED"
    )
    assert _enterprise_controlled_count == 2

    _markdown = render_markdown(_ledger)
    assert "|" in _markdown
    assert "TRUSTED_GENERAL" in _markdown
    assert "ENTERPRISE_CONTROLLED" in _markdown

    print("PASS")