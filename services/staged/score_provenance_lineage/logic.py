"""Score provenance lineage computation and API route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter()


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def get_score_provenance(
    db: Session,
    server_id: str,
    stale_days: int = 30,
) -> Dict[str, Any]:
    """Return score provenance grouped by scoring implementation for a server."""
    score_rows = list(
        db.execute(
            select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
        )
        .scalars()
        .all()
    )

    registry = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    groups: Dict[Tuple[Any, Any, Any], List[McpLlmAxisScore]] = {}
    for row in score_rows:
        key = (
            row.model_version,
            row.adapter_sha256,
            row.decision_rule_version,
        )
        groups.setdefault(key, []).append(row)

    models: List[Dict[str, Any]] = []
    group_times: List[datetime] = []
    for (model_version, adapter_sha256, decision_rule_version), rows in groups.items():
        scored_times = [_as_datetime(row.scored_at) for row in rows]
        valid_times = [value for value in scored_times if value is not None]
        last_scored_at = max(valid_times) if valid_times else None
        if last_scored_at is not None:
            group_times.append(last_scored_at)

        p_top_values = [
            float(row.p_top)
            for row in rows
            if row.p_top is not None
        ]
        models.append(
            {
                "model_version": model_version,
                "adapter_sha256": adapter_sha256,
                "decision_rule_version": decision_rule_version,
                "axis_count": len(rows),
                "last_scored_at": _isoformat(last_scored_at),
                "max_p_top": max(p_top_values) if p_top_values else 0.0,
            }
        )

    models.sort(
        key=lambda item: (
            item["last_scored_at"] is None,
            item["last_scored_at"] or "",
            item["model_version"] or "",
            item["adapter_sha256"] or "",
            item["decision_rule_version"] or "",
        ),
        reverse=True,
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, int(stale_days)))
    stale = any(value < cutoff for value in group_times)

    return {
        "server_id": server_id,
        "risk_tier": registry.risk_tier if registry is not None else None,
        "stale": stale,
        "stale_days": int(stale_days),
        "models": models,
    }


@router.get("/scores/provenance")
def score_provenance(
    server_id: str,
    stale_days: int = 30,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_score_provenance(db, server_id, stale_days)


score_provenance_lineage = get_score_provenance


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    old_time = datetime.now(timezone.utc) - timedelta(days=40)
    try:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-one",
                    axis_name="critical",
                    model_version="model-a",
                    p_top=0.91,
                    scored_at=old_time,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-one",
                    axis_name="danger",
                    model_version="model-a",
                    p_top=0.72,
                    scored_at=old_time,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-one",
                    axis_name="critical",
                    model_version="model-b",
                    p_top=0.63,
                    scored_at=old_time,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-two",
                    axis_name="critical",
                    model_version="model-c",
                    p_top=0.42,
                    scored_at=old_time,
                ),
                McpServerRegistry(
                    server_id="server-one",
                    name="Server One",
                    risk_tier="high",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        db_session = SessionLocal()
        try:
            yield db_session
        finally:
            db_session.close()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get(
        "/api/scores/provenance",
        params={"server_id": "server-one", "stale_days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["models"]) == 2
    assert payload["stale"] is True
    print("PASS")


if __name__ == "__main__":
    _self_test()