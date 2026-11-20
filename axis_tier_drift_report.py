from collections import Counter
from typing import Any

from sqlalchemy import select

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
NON_OVERALL_AXES = tuple(axis for axis in AXIS_NAMES if axis != "overall_risk")


def _label_index(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _axis_tier(index: int | None) -> str:
    if index is not None and index >= 3:
        return "HIGH_RISK_ISOLATED"
    if index is not None and index >= 2:
        return "CAUTION_LIMITED"
    return "TRUSTED_GENERAL"


def _expected_tier(axis_rows: list[dict]) -> str:
    indexes = [
        _label_index(row.get("label_index"))
        for row in axis_rows
        if row.get("axis_name") != "overall_risk"
    ]
    indexes = [index for index in indexes if index is not None]

    if sum(index >= 3 for index in indexes) >= 4:
        return "HIGH_RISK_ISOLATED"
    if sum(index >= 2 for index in indexes) >= 2:
        return "CAUTION_LIMITED"
    return "TRUSTED_GENERAL"


def _latest_axis_rows(axis_rows: list[dict]) -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    for row in axis_rows:
        server_id = str(row.get("server_id", ""))
        axis_name = str(row.get("axis_name", ""))
        key = (server_id, axis_name)
        if key not in latest:
            latest[key] = row
            continue
        current_time = str(latest[key].get("scored_at") or "")
        candidate_time = str(row.get("scored_at") or "")
        if candidate_time >= current_time:
            latest[key] = row
    return latest


def compute_drift(axis_rows: list[dict], registry_rows: list[dict]) -> dict:
    latest = _latest_axis_rows(axis_rows)
    registry: dict[str, dict] = {}
    for row in registry_rows:
        registry[str(row.get("server_id", ""))] = row

    server_ids = sorted(
        {server_id for server_id, _ in latest}
        | set(registry)
    )
    grouped: dict[str, list[dict]] = {server_id: [] for server_id in server_ids}
    for (server_id, _), row in latest.items():
        grouped[server_id].append(row)

    examples: list[dict] = []
    drifted_servers: set[str] = set()
    by_axis: Counter[str] = Counter()

    for server_id in server_ids:
        rows = sorted(
            grouped.get(server_id, []),
            key=lambda row: str(row.get("axis_name", "")),
        )
        non_overall = [
            row for row in rows if row.get("axis_name") != "overall_risk"
        ]
        expected_tier = _expected_tier(rows)
        registry_row = registry.get(server_id)
        registry_tier = (
            str(registry_row.get("risk_tier"))
            if registry_row is not None
            and registry_row.get("risk_tier") is not None
            else None
        )

        if registry_row is None:
            for row in non_overall:
                axis_name = str(row.get("axis_name", ""))
                label = str(row.get("label", ""))
                index = _label_index(row.get("label_index"))
                note = (
                    f"no registry tier is available; the axis label_index "
                    f"{index if index is not None else 'unknown'} implies "
                    f"{_axis_tier(index)} and the published band map gives "
                    f"expected tier {expected_tier}"
                )
                examples.append(
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "axis_label": label,
                        "registry_tier": None,
                        "expected_tier": expected_tier,
                        "note": note,
                    }
                )
                by_axis[axis_name] += 1
                drifted_servers.add(server_id)
            continue

        conflicting_rows = []
        for row in non_overall:
            index = _label_index(row.get("label_index"))
            axis_tier = _axis_tier(index)
            if axis_tier != registry_tier:
                conflicting_rows.append((row, axis_tier))

        if conflicting_rows:
            for row, axis_tier in conflicting_rows:
                axis_name = str(row.get("axis_name", ""))
                label = str(row.get("axis_label", row.get("label", "")))
                index = _label_index(row.get("label_index"))
                note = (
                    f"axis label_index {index if index is not None else 'unknown'} "
                    f"implies {axis_tier}, but the registry tier is "
                    f"{registry_tier}; published band expected tier is "
                    f"{expected_tier}"
                )
                examples.append(
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "axis_label": label,
                        "registry_tier": registry_tier,
                        "expected_tier": expected_tier,
                        "note": note,
                    }
                )
                by_axis[axis_name] += 1
                drifted_servers.add(server_id)
        elif registry_tier != expected_tier and non_overall:
            for row in non_overall:
                axis_name = str(row.get("axis_name", ""))
                label = str(row.get("label", ""))
                note = (
                    f"published band expected tier {expected_tier} differs "
                    f"from registry tier {registry_tier}"
                )
                examples.append(
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "axis_label": label,
                        "registry_tier": registry_tier,
                        "expected_tier": expected_tier,
                        "note": note,
                    }
                )
                by_axis[axis_name] += 1
                drifted_servers.add(server_id)

    examples.sort(key=lambda item: (item["server_id"], item["axis_name"]))
    return {
        "servers_checked": len(server_ids),
        "drifted": len(drifted_servers),
        "by_axis": {
            axis_name: by_axis[axis_name]
            for axis_name in sorted(by_axis)
        },
        "examples": examples,
    }


def _markdown_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(report: dict) -> str:
    lines = [
        "| server_id | axis | label | expected tier | registry tier |",
        "|---|---|---|---|---|",
    ]
    examples = report.get("examples", [])
    for example in sorted(
        examples,
        key=lambda item: (str(item.get("server_id", "")), str(item.get("axis_name", ""))),
    ):
        lines.append(
            "| {server_id} | {axis_name} | {axis_label} | {expected_tier} | {registry_tier} |".format(
                server_id=_markdown_cell(example.get("server_id")),
                axis_name=_markdown_cell(example.get("axis_name")),
                axis_label=_markdown_cell(example.get("axis_label")),
                expected_tier=_markdown_cell(example.get("expected_tier")),
                registry_tier=_markdown_cell(example.get("registry_tier")),
            )
        )
    return "\n".join(lines)


def build_report(session) -> dict:
    score_rows = session.execute(
        select(McpLlmAxisScore).order_by(
            McpLlmAxisScore.scored_at.asc(),
            McpLlmAxisScore.id.asc(),
        )
    ).scalars().all()
    registry_rows = session.execute(
        select(McpServerRegistry)
    ).scalars().all()

    axis_rows = [
        {
            "server_id": str(row.server_id),
            "axis_name": str(row.axis_name),
            "label": str(row.label) if row.label is not None else "",
            "label_index": row.label_index,
            "p_top": row.p_top,
            "scored_at": row.scored_at,
        }
        for row in score_rows
    ]
    registry_data = [
        {
            "server_id": str(row.server_id),
            "name": row.name,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
        }
        for row in registry_rows
    ]
    return compute_drift(axis_rows, registry_data)


if __name__ == "__main__":
    synthetic_axes = [
        {
            "server_id": "consistent",
            "axis_name": axis,
            "label": "LOW",
            "label_index": 1,
            "p_top": 0.1,
            "scored_at": "2024-01-01T00:00:00Z",
        }
        for axis in NON_OVERALL_AXES
    ]
    synthetic_axes.extend(
        [
            {
                "server_id": "drifting",
                "axis_name": axis,
                "label": "LOW",
                "label_index": 1,
                "p_top": 0.1,
                "scored_at": "2024-01-01T00:00:00Z",
            }
            for axis in NON_OVERALL_AXES
        ]
    )
    synthetic_axes.append(
        {
            "server_id": "drifting",
            "axis_name": "auth_strength",
            "label": "CRITICAL",
            "label_index": 4,
            "p_top": 0.9,
            "scored_at": "2024-01-02T00:00:00Z",
        }
    )
    synthetic_axes.append(
        {
            "server_id": "missing-axes",
            "axis_name": "auth_strength",
            "label": "LOW",
            "label_index": 1,
            "p_top": 0.1,
            "scored_at": "2024-01-01T00:00:00Z",
        }
    )

    synthetic_registry = [
        {
            "server_id": "consistent",
            "name": "consistent",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.9,
        },
        {
            "server_id": "drifting",
            "name": "drifting",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.8,
        },
        {
            "server_id": "missing-axes",
            "name": "missing-axes",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.7,
        },
        {
            "server_id": "registry-only",
            "name": "registry-only",
            "risk_tier": "TRUSTED_GENERAL",
            "trust_score": 0.6,
        },
    ]

    report = compute_drift(synthetic_axes, synthetic_registry)
    markdown = render_markdown(report)
    assert report["servers_checked"] == 4
    assert report["drifted"] >= 1
    assert report["by_axis"].get("auth_strength", 0) > 0
    assert "| server_id | axis | label | expected tier | registry tier |" in markdown
    assert "drifting" in markdown
    print("PASS")