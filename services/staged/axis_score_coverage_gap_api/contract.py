from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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

router = APIRouter(prefix="/api")


def _timestamp_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    value_text = str(value)
    try:
        parsed = datetime.fromisoformat(value_text.replace("Z", "+00:00"))
        return parsed.isoformat()
    except ValueError:
        return value_text


def _unique_server_ids(rows: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        server_id = row[0] if not hasattr(row, "_mapping") else row._mapping["server_id"]
        if server_id is not None and server_id not in seen:
            seen.add(server_id)
            result.append(server_id)
    return result


def calculate_coverage_gaps(session: Session) -> Dict[str, Any]:
    registry_result = session.execute(
        text("SELECT server_id, risk_tier FROM mcp_server_registry")
    ).all()
    server_ids = _unique_server_ids(registry_result)

    score_result = session.execute(
        text(
            """
            SELECT server_id, axis_name, p_top, scored_at, model_version,
                   decision_rule_version
            FROM mcp_llm_axis_scores
            WHERE axis_name IN (
                'overall_risk',
                'auth_strength',
                'capability_breadth',
                'data_sensitivity',
                'network_egress',
                'maintainer_trust',
                'exploit_surface'
            )
            """
        )
    ).all()

    scores_by_axis: Dict[str, List[Any]] = {axis: [] for axis in AXIS_NAMES}
    scored_servers: Dict[str, Set[str]] = {axis: set() for axis in AXIS_NAMES}
    model_versions: Dict[str, Set[Any]] = {axis: set() for axis in AXIS_NAMES}
    latest_scored_at: Dict[str, Any] = {axis: None for axis in AXIS_NAMES}

    for row in score_result:
        mapping = row._mapping
        server_id = mapping["server_id"]
        axis_name = mapping["axis_name"]
        if axis_name not in scores_by_axis or server_id is None:
            continue
        scores_by_axis[axis_name].append(row)
        scored_servers[axis_name].add(server_id)
        if mapping["model_version"] is not None:
            model_versions[axis_name].add(mapping["model_version"])
        scored_at = mapping["scored_at"]
        if scored_at is not None:
            current = latest_scored_at[axis_name]
            if current is None or scored_at > current:
                latest_scored_at[axis_name] = scored_at

    axes: List[Dict[str, Any]] = []
    for axis_name in AXIS_NAMES:
        servers_scored = len(scored_servers[axis_name])
        total_servers = len(server_ids)
        servers_missing = total_servers - servers_scored
        coverage_pct = (
            float(servers_scored) / float(total_servers) * 100.0
            if total_servers
            else 0.0
        )
        axes.append(
            {
                "axis_name": axis_name,
                "servers_scored": servers_scored,
                "servers_missing": servers_missing,
                "coverage_pct": coverage_pct,
                "latest_scored_at": _timestamp_iso(latest_scored_at[axis_name]),
                "distinct_model_versions": len(model_versions[axis_name]),
            }
        )

    server_axis_sets: Dict[str, Set[str]] = {server_id: set() for server_id in server_ids}
    for axis_name in AXIS_NAMES:
        for server_id in scored_servers[axis_name]:
            if server_id in server_axis_sets:
                server_axis_sets[server_id].add(axis_name)

    fully_scored_servers = sum(
        1 for axis_set in server_axis_sets.values() if len(axis_set) == len(AXIS_NAMES)
    )
    partially_scored_servers = sum(
        1 for axis_set in server_axis_sets.values() if 0 < len(axis_set) < len(AXIS_NAMES)
    )
    never_scored_servers = sum(1 for axis_set in server_axis_sets.values() if not axis_set)

    return {
        "total_servers": len(server_ids),
        "axes": axes,
        "fully_scored_servers": fully_scored_servers,
        "partially_scored_servers": partially_scored_servers,
        "never_scored_servers": never_scored_servers,
    }


@router.get("/scoring/coverage-gaps")
def get_scoring_coverage_gaps(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return calculate_coverage_gaps(session)


app = FastAPI()
app.include_router(router)


def _run_contract_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    p_top FLOAT,
                    scored_at DATETIME,
                    model_version VARCHAR,
                    decision_rule_version VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id, risk_tier) VALUES "
                "('server-complete', 'high'), "
                "('server-partial', 'medium'), "
                "('server-never', 'low')"
            )
        )
        for axis_name in AXIS_NAMES:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, p_top, scored_at, model_version,
                         decision_rule_version)
                    VALUES
                        ('server-complete', :axis_name, 0.8, :scored_at,
                         'model-v1', 'rules-v1')
                    """
                ),
                {
                    "axis_name": axis_name,
                    "scored_at": datetime(2024, 1, 1, 12, 0, 0),
                },
            )
        for axis_name in (
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (server_id, axis_name, p_top, scored_at, model_version,
                         decision_rule_version)
                    VALUES
                        ('server-partial', :axis_name, 0.4, :scored_at,
                         'model-v2', 'rules-v1')
                    """
                ),
                {
                    "axis_name": axis_name,
                    "scored_at": datetime(2024, 1, 2, 12, 0, 0),
                },
            )

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/coverage-gaps")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["axes"]) == 7
        assert payload["total_servers"] == 3
        axes = {axis["axis_name"]: axis for axis in payload["axes"]}
        assert axes["overall_risk"]["servers_missing"] == 2
        assert axes["auth_strength"]["servers_missing"] == 1
        assert payload["fully_scored_servers"] == 1
        assert payload["partially_scored_servers"] == 1
        assert payload["never_scored_servers"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_contract_test()