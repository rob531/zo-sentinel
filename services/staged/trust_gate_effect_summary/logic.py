"""Data and computation layer for the trust-gate effect summary service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from services.trust_gating_override.logic import trust_gate
except ImportError:
    try:
        from services.trust_gating_override import trust_gate
    except ImportError:
        try:
            from trust_gating_override.logic import trust_gate
        except ImportError:
            from trust_gating_override import trust_gate


def _member(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "trusted" if value else "untrusted"
    return str(value).strip()


def _gate_candidates(result: Any) -> List[tuple[int, str]]:
    candidates: List[tuple[int, str]] = []

    for priority, name in (
        (0, "gated_verdict"),
        (0, "published_verdict"),
        (0, "overall_verdict"),
        (0, "verdict"),
        (1, "published_overall_risk"),
    ):
        value = _member(result, name)
        if value is not None:
            candidates.append((priority, _as_text(value)))

    trusted = _member(result, "trusted")
    if trusted is not None:
        candidates.append((2, _as_text(trusted)))

    if not candidates and isinstance(result, Sequence) and not isinstance(
        result, (str, bytes, bytearray)
    ):
        for index, value in enumerate(result):
            if value is not None:
                candidates.append((1 + index, _as_text(value)))

    return candidates


def _gated_verdict(result: Any, stored_verdict: Any) -> str:
    candidates = _gate_candidates(result)
    if not candidates:
        return ""

    stored = _as_text(stored_verdict).casefold()
    for _, value in candidates:
        if value.casefold() == stored:
            return value
    return min(candidates, key=lambda item: item[0])[1]


def _has_missing_axes(result: Any) -> bool:
    missing = _member(result, "missing_axes")
    if missing is None:
        return False
    if isinstance(missing, (str, bytes, bytearray)):
        return bool(missing.strip())
    try:
        return bool(missing)
    except TypeError:
        return True


def _latest_axis_rows(db: Session) -> Dict[str, Dict[str, McpLlmAxisScore]]:
    rows = db.execute(
        select(McpLlmAxisScore).order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    ).scalars()

    latest: Dict[str, Dict[str, McpLlmAxisScore]] = {}
    for row in rows:
        server_id = str(row.server_id)
        axis_name = str(row.axis_name)
        server_axes = latest.setdefault(server_id, {})
        current = server_axes.get(axis_name)
        if current is None or _axis_order(row) > _axis_order(current):
            server_axes[axis_name] = row
    return latest


def _axis_order(row: McpLlmAxisScore) -> tuple[bool, str, str]:
    scored_at = row.scored_at
    if scored_at is None:
        timestamp = ""
    elif hasattr(scored_at, "isoformat"):
        timestamp = str(scored_at.isoformat())
    else:
        timestamp = str(scored_at)
    return (bool(timestamp), timestamp, str(row.id or ""))


def _effect_summary(db: Session, gate: Any) -> Dict[str, Any]:
    servers = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.verdict,
        )
    ).all()
    latest_axes = _latest_axis_rows(db)

    agree = 0
    overridden = 0
    missing_axes = 0
    examples: List[Dict[str, Any]] = []

    for server_id, name, url, stored_verdict in servers:
        axis_map = {
            axis_name: row.label or ""
            for axis_name, row in latest_axes.get(str(server_id), {}).items()
        }
        stored_text = _as_text(stored_verdict)
        gated_text = stored_text

        if not axis_map:
            missing_axes += 1
        else:
            try:
                gate_result = gate(url, name, axis_map)
                candidate = _gated_verdict(gate_result, stored_verdict)
                if _has_missing_axes(gate_result) or not candidate:
                    missing_axes += 1
                    gated_text = stored_text
                else:
                    gated_text = candidate
            except Exception:
                missing_axes += 1
                gated_text = stored_text

        if gated_text.casefold() == stored_text.casefold():
            agree += 1
        else:
            overridden += 1

        if len(examples) < 20:
            examples.append(
                {
                    "server_id": str(server_id),
                    "stored_verdict": stored_text,
                    "gated_verdict": gated_text,
                }
            )

    total = len(servers)
    override_rate = float(overridden / total) if total else 0.0
    return {
        "counts": {
            "agree": agree,
            "overridden": overridden,
            "missing_axes": missing_axes,
            "total": total,
            "override_rate": override_rate,
        },
        "examples": examples,
    }


def trust_gate_effect_summary(db: Session) -> Dict[str, Any]:
    """Summarize how the trust gate changes registry verdicts."""
    return _effect_summary(db, trust_gate)


def get_trust_gate_effect_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the trust-gate effect summary computation."""
    return trust_gate_effect_summary(db)


def build_effect_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for consumers that use a builder-style name."""
    return trust_gate_effect_summary(db)


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    server_id = "server-1"
    name = "trusted-server"
    url = "https://example.com/mcp"
    registry = McpServerRegistry(
        server_id=server_id,
        name=name,
        url=url,
        verdict="trusted",
        risk_tier="low",
        trust_score=90,
        last_assessed=datetime.now(timezone.utc),
    )
    registry_2 = McpServerRegistry(
        server_id="server-2",
        name="scored-server",
        url="https://example.org/mcp",
        verdict="review",
        risk_tier="medium",
        trust_score=60,
        last_assessed=datetime.now(timezone.utc),
    )
    registry_3 = McpServerRegistry(
        server_id="server-3",
        name="unscored-server",
        url="https://example.net/mcp",
        verdict="unknown",
        risk_tier="unknown",
        trust_score=None,
        last_assessed=None,
    )
    scores = [
        McpLlmAxisScore(
            id=1,
            server_id=server_id,
            axis_name="security",
            label="pass",
            model_version="test",
        ),
        McpLlmAxisScore(
            id=2,
            server_id=server_id,
            axis_name="reliability",
            label="pass",
            model_version="test",
        ),
        McpLlmAxisScore(
            id=3,
            server_id="server-2",
            axis_name="security",
            label="review",
            model_version="test",
        ),
        McpLlmAxisScore(
            id=4,
            server_id="server-2",
            axis_name="reliability",
            label="pass",
            model_version="test",
        ),
    ]

    with TestingSession() as db:
        db.add_all([registry, registry_2, registry_3, *scores])
        db.commit()

    that_app = FastAPI()

    @that_app.get("/effect-summary")
    def effect_summary(db: Session = Depends(get_session)) -> Dict[str, Any]:
        return trust_gate_effect_summary(db)

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/effect-summary")
        assert response.status_code == 200
        counts = response.json()["counts"]
        assert counts["total"] == 3
        assert counts["missing_axes"] >= 1
        assert isinstance(counts["override_rate"], float)
        assert 0.0 <= counts["override_rate"] <= 1.0

    print("PASS")


if __name__ == "__main__":
    _self_test()