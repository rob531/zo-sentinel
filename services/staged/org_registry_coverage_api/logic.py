from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, Org, Perspective

try:
    from .contract import OrgRegistryCoverageResponse
except ImportError:
    from contract import OrgRegistryCoverageResponse


router = APIRouter(prefix="/api")


def _string_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _org_ids_from_meta(meta: Any) -> set[str]:
    if not isinstance(meta, dict):
        return set()

    result: set[str] = set()
    for key in ("org_id", "orgId", "organization_id", "assigned_org_id"):
        value = meta.get(key)
        if value is not None:
            result.add(str(value))

    for key in ("org_ids", "orgIds", "organization_ids"):
        value = meta.get(key)
        if isinstance(value, (list, tuple, set)):
            result.update(str(item) for item in value if item is not None)
        elif value is not None:
            result.add(str(value))

    return result


def _explicit_server_ids(value: Any) -> set[str]:
    result: set[str] = set()

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {
                "server_id",
                "server_ids",
                "servers",
                "mcp_server_id",
                "mcp_server_ids",
            }:
                if isinstance(child, (list, tuple, set)):
                    result.update(str(item) for item in child if item is not None)
                elif isinstance(child, dict):
                    result.update(_explicit_server_ids(child))
                elif child is not None:
                    result.add(str(child))
            else:
                result.update(_explicit_server_ids(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            result.update(_explicit_server_ids(child))

    return result


def _filter_constraints(value: Any) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    known = {
        "risk_tier",
        "risk_tiers",
        "registry_source",
        "registry_sources",
        "verdict",
        "verdicts",
        "name",
        "names",
        "server_id",
        "server_ids",
    }

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in known:
                constraints[normalized] = child
            elif isinstance(child, (dict, list, tuple, set)):
                nested = _filter_constraints(child)
                for nested_key, nested_value in nested.items():
                    constraints.setdefault(nested_key, nested_value)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            nested = _filter_constraints(child)
            for nested_key, nested_value in nested.items():
                constraints.setdefault(nested_key, nested_value)

    return constraints


def _allowed_values(value: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            values.update(_allowed_values(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            values.update(_allowed_values(child))
    elif value is not None:
        values.add(str(value))
    return values


def _matches_constraints(row: McpServerRegistry, constraints: dict[str, Any]) -> bool:
    field_aliases = {
        "risk_tiers": "risk_tier",
        "registry_sources": "registry_source",
        "verdicts": "verdict",
        "names": "name",
        "server_ids": "server_id",
    }

    for key, expected in constraints.items():
        if key in {"server_id", "server_ids"}:
            continue

        field = field_aliases.get(key, key)
        if not hasattr(row, field):
            continue

        actual = getattr(row, field)
        if actual is None:
            return False

        if isinstance(expected, dict):
            allowed = expected.get("include") or expected.get("values")
            if allowed is not None:
                allowed_values = _allowed_values(allowed)
                if str(actual).upper() not in {item.upper() for item in allowed_values}:
                    return False

            minimum = expected.get("min", expected.get("gte"))
            maximum = expected.get("max", expected.get("lte"))
            try:
                numeric_actual = float(actual)
                if minimum is not None and numeric_actual < float(minimum):
                    return False
                if maximum is not None and numeric_actual > float(maximum):
                    return False
            except (TypeError, ValueError):
                pass
        else:
            allowed_values = _allowed_values(expected)
            if allowed_values and str(actual).upper() not in {
                item.upper() for item in allowed_values
            }:
                return False

    return True


def org_registry_coverage(
    org_id: str | None = None,
    session: Session | None = None,
) -> dict:
    owns_session = session is None
    if session is None:
        session = next(get_session())

    try:
        org_query = session.query(Org)
        if org_id is not None:
            org_query = org_query.filter(Org.id == org_id)
        orgs = org_query.all()

        registry_rows = session.query(McpServerRegistry).all()
        perspectives = session.query(Perspective).all()

        registry_columns = {
            column.key for column in inspect(McpServerRegistry).columns
        }
        direct_org_column = "org_id" in registry_columns

        registry_by_server = {
            str(row.server_id): row for row in registry_rows
        }
        perspective_org_by_id = {
            str(perspective.id): _string_id(perspective.org_id)
            for perspective in perspectives
            if perspective.org_id is not None
        }

        explicit_by_org: dict[str, set[str]] = {}
        generic_perspectives: dict[str, list[tuple[set[str], dict[str, Any]]]] = {}

        for perspective in perspectives:
            owner = _string_id(perspective.org_id)
            if owner is None:
                continue
            filters = perspective.facet_filters
            explicit = _explicit_server_ids(filters)
            if explicit:
                explicit_by_org.setdefault(owner, set()).update(explicit)
            else:
                generic_perspectives.setdefault(owner, []).append(
                    (_explicit_server_ids(filters), _filter_constraints(filters))
                )

        assignment_mode = direct_org_column
        if not assignment_mode:
            assignment_mode = any(
                _org_ids_from_meta(getattr(row, "meta", None))
                for row in registry_rows
            )

        visible_by_org: dict[str, set[str]] = {
            str(org.id): set() for org in orgs
        }

        for row in registry_rows:
            server_key = str(row.server_id)
            assigned_orgs: set[str] = set()

            if direct_org_column:
                row_org_id = _string_id(getattr(row, "org_id", None))
                if row_org_id is not None:
                    assigned_orgs.add(row_org_id)
            else:
                assigned_orgs.update(_org_ids_from_meta(getattr(row, "meta", None)))

            meta = getattr(row, "meta", None)
            if isinstance(meta, dict):
                perspective_id = meta.get("perspective_id") or meta.get("perspectiveId")
                if perspective_id is not None:
                    owner = perspective_org_by_id.get(str(perspective_id))
                    if owner is not None:
                        assigned_orgs.add(owner)

            for owner in assigned_orgs:
                if owner in visible_by_org:
                    visible_by_org[owner].add(server_key)

            if assignment_mode:
                continue

            for owner, perspective_filters in generic_perspectives.items():
                if owner not in visible_by_org:
                    continue
                for explicit_ids, constraints in perspective_filters:
                    if explicit_ids:
                        if server_key in explicit_ids:
                            visible_by_org[owner].add(server_key)
                    elif _matches_constraints(row, constraints):
                        visible_by_org[owner].add(server_key)

        rows: list[dict[str, Any]] = []
        for org in orgs:
            org_key = str(org.id)
            visible = [
                registry_by_server[server_id]
                for server_id in visible_by_org.get(org_key, set())
                if server_id in registry_by_server
            ]

            critical_count = sum(
                1
                for row in visible
                if str(row.risk_tier or "").upper() == "CRITICAL"
            )
            high_risk_count = sum(
                1
                for row in visible
                if str(row.risk_tier or "").upper() == "HIGH"
            )
            assessed = [
                _timestamp(row.last_assessed)
                for row in visible
                if row.last_assessed is not None
            ]

            rows.append(
                {
                    "org_id": org_key,
                    "org_name": str(org.name),
                    "server_count": len(visible),
                    "critical_count": critical_count,
                    "high_risk_count": high_risk_count,
                    "last_assessed_at": max(assessed) if assessed else None,
                }
            )

        rows.sort(
            key=lambda row: (
                -row["server_count"],
                row["org_name"].casefold(),
                row["org_id"],
            )
        )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "orgs": rows,
            "total_orgs": len(rows),
        }
    finally:
        if owns_session:
            close = getattr(session, "close", None)
            if close is not None:
                close()


@router.get(
    "/orgs/registry-coverage",
    response_model=OrgRegistryCoverageResponse,
)
def get_org_registry_coverage(
    org_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    return org_registry_coverage(org_id=org_id, session=session)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    registry_columns = {
        column.key for column in inspect(McpServerRegistry).columns
    }

    with TestSession() as test_session:
        test_session.add_all(
            [
                Org(id="org-1", name="Organization One"),
                Org(id="org-2", name="Organization Two"),
            ]
        )
        test_session.flush()

        assigned_kwargs: dict[str, Any]
        if "org_id" in registry_columns:
            assigned_kwargs = {"org_id": "org-1"}
        else:
            assigned_kwargs = {"meta": {"org_id": "org-1"}}

        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="server-critical",
                    risk_tier="CRITICAL",
                    **assigned_kwargs,
                ),
                McpServerRegistry(
                    server_id="server-high",
                    risk_tier="HIGH",
                    **assigned_kwargs,
                ),
                McpServerRegistry(
                    server_id="server-unassigned-1",
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="server-unassigned-2",
                    risk_tier="MEDIUM",
                ),
            ]
        )
        test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/orgs/registry-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_orgs"] == 2
        org_one = next(row for row in payload["orgs"] if row["org_id"] == "org-1")
        assert org_one["server_count"] == 2
        assert org_one["critical_count"] == 1
        assert org_one["high_risk_count"] == 1

    print("PASS")