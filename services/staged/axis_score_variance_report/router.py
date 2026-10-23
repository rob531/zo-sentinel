from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import get_axis_score_variance_report

router = APIRouter(prefix="/api", tags=["axis_score_variance_report"])


@router.get("/scoring/variance", response_model=dict[str, Any])
def get_variance_report(
    days: int = Query(default=7, ge=1),
    min_delta: float = Query(default=5.0, ge=0.0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_axis_score_variance_report(
        session=session,
        days=days,
        min_delta=min_delta,
    )


def _run_self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        axis_names = ["accuracy", "relevance", "coherence", "toxicity", "bias"]
        score_id = 1

        with testing_session() as db:
            servers = [
                McpServerRegistry(server_id="srv1", name="Server One"),
                McpServerRegistry(server_id="srv2", name="Server Two"),
                McpServerRegistry(server_id="srv3", name="Server Three"),
            ]
            db.add_all(servers)

            for server in servers:
                for axis_index, axis_name in enumerate(axis_names):
                    baseline = 20.0 + axis_index
                    shift = (
                        10.0
                        if server.server_id == "srv2" and axis_name == "accuracy"
                        else 0.0
                    )
                    for window_name, scored_at, score in (
                        ("prior", now - timedelta(days=8), baseline),
                        ("current", now - timedelta(days=1), baseline + shift),
                    ):
                        db.add(
                            McpLlmAxisScore(
                                id=score_id,
                                server_id=server.server_id,
                                axis_name=axis_name,
                                label=axis_name.title(),
                                label_index=axis_index,
                                probs={},
                                p_top=score,
                                p_critical=0.0,
                                p_danger=0.0,
                                escalated=False,
                                escalated_to=None,
                                decision_rule_version="variance-self-test-v1",
                                model_version=f"{window_name}-{axis_name}",
                                adapter_sha256="a" * 64,
                                scored_at=scored_at,
                            )
                        )
                        score_id += 1
            db.commit()

        def override_get_session():
            db = testing_session()
            try:
                yield db
            finally:
                db.close()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/scoring/variance",
                params={"days": 7, "min_delta": 5.0},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["period"] == "last_7_days", payload
        assert len(payload["servers"]) == 3, payload
        assert all(len(server["axes"]) == 5 for server in payload["servers"]), payload
        flagged_servers = [
            server["server_id"]
            for server in payload["servers"]
            if server["overall"]["flagged"]
            or any(axis["flagged"] for axis in server["axes"])
        ]
        assert flagged_servers == ["srv2"], payload
    finally:
        engine.dispose()


if __name__ == "__main__":
    import sys

    try:
        _run_self_test()
    except Exception as exc:
        print(f"FAIL: {exc!r}", file=sys.stderr)
        sys.exit(1)
    print("PASS")