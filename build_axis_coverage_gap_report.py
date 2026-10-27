from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db import get_session
from app.models import McpLlmAxisScore


EXPECTED_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def build_report(session) -> dict:
    generated_at = datetime.now(timezone.utc)
    stale_cutoff = generated_at - timedelta(days=30)
    rows = session.query(McpLlmAxisScore).all()

    all_server_ids = {
        server_id
        for row in rows
        for server_id in [getattr(row, "server_id", None)]
        if server_id is not None
    }
    distinct_servers = len(all_server_ids)

    rows_by_axis = {axis: [] for axis in EXPECTED_AXES}
    unexpected_axis_names = set()
    global_rule_versions = Counter()

    for row in rows:
        axis_name = getattr(row, "axis_name", None)
        if axis_name in rows_by_axis:
            rows_by_axis[axis_name].append(row)
        elif axis_name is not None:
            unexpected_axis_names.add(str(axis_name))

        version = getattr(row, "decision_rule_version", None)
        if version is not None:
            global_rule_versions[str(version)] += 1

    axes = []
    servers_missing_axis = {}

    for axis_name in EXPECTED_AXES:
        axis_rows = rows_by_axis[axis_name]
        scored_server_ids = {
            server_id
            for row in axis_rows
            for server_id in [getattr(row, "server_id", None)]
            if server_id is not None
        }
        missing_servers = distinct_servers - len(scored_server_ids)
        coverage_pct = (
            float(len(scored_server_ids) * 100.0 / distinct_servers)
            if distinct_servers
            else 0.0
        )

        decision_rule_versions = Counter()
        model_versions = Counter()
        escalated = 0
        stale_rows_30d = 0

        for row in axis_rows:
            if bool(getattr(row, "escalated", False)):
                escalated += 1

            rule_version = getattr(row, "decision_rule_version", None)
            if rule_version is not None:
                decision_rule_versions[str(rule_version)] += 1

            model_version = getattr(row, "model_version", None)
            if model_version is not None:
                model_versions[str(model_version)] += 1

            scored_at = getattr(row, "scored_at", None)
            if scored_at is not None:
                if scored_at.tzinfo is None:
                    scored_at = scored_at.replace(tzinfo=timezone.utc)
                if scored_at < stale_cutoff:
                    stale_rows_30d += 1

        axes.append(
            {
                "axis": axis_name,
                "scored_servers": len(scored_server_ids),
                "missing_servers": missing_servers,
                "coverage_pct": round(coverage_pct, 2),
                "escalated": escalated,
                "stale_rows_30d": stale_rows_30d,
                "decision_rule_versions": dict(
                    sorted(decision_rule_versions.items())
                ),
                "model_versions": dict(sorted(model_versions.items())),
            }
        )
        servers_missing_axis[axis_name] = missing_servers

    if global_rule_versions:
        top_decision_rule_version = sorted(
            global_rule_versions.items(),
            key=lambda item: (-item[1], item[0]),
        )[0][0]
    else:
        top_decision_rule_version = None

    return {
        "generated_at": generated_at.isoformat(),
        "distinct_servers": distinct_servers,
        "axis_rows": len(rows),
        "axes": axes,
        "servers_missing_axis": servers_missing_axis,
        "unexpected_axes": sorted(unexpected_axis_names),
        "top_decision_rule_version": top_decision_rule_version,
    }


def render_report(report: dict) -> str:
    lines = [
        "AXIS COVERAGE GAP REPORT",
        f"Generated at: {report['generated_at']}",
        "",
        "TOTALS",
        f"Distinct servers: {report['distinct_servers']}",
        f"Axis rows: {report['axis_rows']}",
        f"Top decision rule version: {report['top_decision_rule_version'] or 'none'}",
        "",
        "PER-AXIS TABLE",
        "Axis | Scored servers | Missing servers | Coverage % | Escalated | Stale rows 30d",
        "-" * 91,
    ]

    for axis in report["axes"]:
        lines.append(
            "{axis} | {scored_servers} | {missing_servers} | "
            "{coverage_pct:.2f} | {escalated} | {stale_rows_30d}".format(
                axis=axis["axis"],
                scored_servers=axis["scored_servers"],
                missing_servers=axis["missing_servers"],
                coverage_pct=axis["coverage_pct"],
                escalated=axis["escalated"],
                stale_rows_30d=axis["stale_rows_30d"],
            )
        )
        rule_versions = ", ".join(
            f"{version}={count}"
            for version, count in sorted(
                axis["decision_rule_versions"].items()
            )
        )
        model_versions = ", ".join(
            f"{version}={count}"
            for version, count in sorted(axis["model_versions"].items())
        )
        lines.append(f"  Decision rule versions: {rule_versions or 'none'}")
        lines.append(f"  Model versions: {model_versions or 'none'}")

    lines.extend(
        [
            "",
            "MISSING SERVERS",
        ]
    )
    for axis_name, count in report["servers_missing_axis"].items():
        lines.append(f"{axis_name}: {count}")

    lines.extend(["", "UNEXPECTED AXES"])
    if report["unexpected_axes"]:
        lines.extend(report["unexpected_axes"])
    else:
        lines.append("none")

    lines.append("REPORT OK")
    return "\n".join(lines)


def main():
    from fastapi import Depends, FastAPI

    class _MemoryQuery:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class _MemorySession:
        def __init__(self, rows):
            self.rows = rows

        def query(self, model):
            if model is not McpLlmAxisScore:
                raise AssertionError("Unexpected model queried")
            return _MemoryQuery(self.rows)

    now = datetime.now(timezone.utc)
    rows = []
    for server_id in ("server-1", "server-2"):
        for axis_name in EXPECTED_AXES:
            if server_id == "server-1" and axis_name == "exploit_surface":
                continue
            rows.append(
                McpLlmAxisScore(
                    server_id=server_id,
                    axis_name=axis_name,
                    label="test",
                    label_index=0,
                    probs=None,
                    p_top=0.1,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=axis_name == "overall_risk"
                    and server_id == "server-1",
                    escalated_to=None,
                    decision_rule_version="v3"
                    if axis_name == "overall_risk"
                    and server_id == "server-1"
                    else "v2",
                    model_version="test-model",
                    scored_at=now,
                )
            )

    memory_session = _MemorySession(rows)
    that_app = FastAPI()

    def override_get_session():
        yield memory_session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/self-test")
    def self_test_route(session=Depends(get_session)):
        return build_report(session)

    report = build_report(memory_session)
    assert report["distinct_servers"] == 2
    assert [axis["axis"] for axis in report["axes"]] == EXPECTED_AXES
    assert report["servers_missing_axis"]["exploit_surface"] == 1
    assert all(
        isinstance(axis["coverage_pct"], float)
        and 0.0 <= axis["coverage_pct"] <= 100.0
        for axis in report["axes"]
    )
    assert report["unexpected_axes"] == []
    assert "REPORT OK" in render_report(report)
    print("PASS")


if __name__ == "__main__":
    main()