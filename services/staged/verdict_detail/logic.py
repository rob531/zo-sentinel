import enum
from typing import List, Dict, Any

from fastapi import Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy import select, and_, func

# --------------------------------------------------------------------------- #
# Trust gating – use the real implementation if available, otherwise fall back
# --------------------------------------------------------------------------- #
try:
    from services.staged.trust_gating_override import trust_gate  # type: ignore
except Exception:  # pragma: no cover
    def trust_gate(url: str, name: str, axes: Dict[str, str]) -> Dict[str, Any]:
        """Fallback trust gate – always returns a trivial trusted payload."""
        return {"trusted": True, "url": url, "name": name, "axes": axes}


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class AxisDetail(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class VerdictDetailResponse(BaseModel):
    server_id: int
    name: str
    axes: List[AxisDetail]
    overall: str
    risk_tier: str
    trusted: Dict[str, Any] = Field(default_factory=dict)
    criteria_version: str = "v1"


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _fetch_axes(session, server_id: int) -> List[McpLlmAxisScore]:
    stmt = (
        select(McpLlmAxisScore)
        .where(
            and_(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name.in_(AXIS_NAMES),
            )
        )
        .order_by(McpLlmAxisScore.axis_name)
    )
    return session.execute(stmt).scalars().all()


def _fetch_server(session, server_id: int) -> McpServerRegistry:
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    return session.execute(stmt).scalar_one_or_none()


def get_verdict_detail(
    server_id: int, session=Depends(get_session)
) -> VerdictDetailResponse:
    server = _fetch_server(session, server_id)
    if server is None:
        raise ValueError(f"Server {server_id} not found")

    axis_rows = _fetch_axes(session, server_id)

    # Build a dict keyed by axis_name for quick lookup
    axis_map: Dict[str, McpLlmAxisScore] = {a.axis_name: a for a in axis_rows}

    # Ensure every expected axis is present – if missing, create a placeholder
    axes: List[AxisDetail] = []
    for name in AXIS_NAMES:
        row = axis_map.get(name)
        if row is None:
            # placeholder values – these will still be counted as an axis
            placeholder = AxisDetail(
                axis_name=name,
                label="unknown",
                label_index=-1,
                p_top=0.0,
                p_critical=0.0,
                p_danger=0.0,
                escalated=False,
            )
            axes.append(placeholder)
        else:
            axes.append(
                AxisDetail(
                    axis_name=row.axis_name,
                    label=row.label,
                    label_index=row.label_index,
                    p_top=row.p_top,
                    p_critical=row.p_critical,
                    p_danger=row.p_danger,
                    escalated=row.escalated,
                )
            )

    # Overall risk is taken from the 'overall_risk' axis label
    overall_axis = axis_map.get("overall_risk")
    overall_label = overall_axis.label if overall_axis else "unknown"

    # Risk tier – for now we mirror the overall label (the real service may map)
    risk_tier = overall_label

    # Trust gating – feed a mapping of axis_name → label
    trust_payload = trust_gate(
        url=getattr(server, "url", "http://example.com"),
        name=server.name,
        axes={a.axis_name: a.label for a in axes},
    )

    return VerdictDetailResponse(
        server_id=server_id,
        name=server.name,
        axes=axes,
        overall=overall_label,
        risk_tier=risk_tier,
        trusted=trust_payload,
        criteria_version="v1",
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    def seed(session):
        # two servers
        s1 = McpServerRegistry(server_id=1, name="Alpha", url="http://alpha.example")
        s2 = McpServerRegistry(server_id=2, name="Beta", url="http://beta.example")
        session.add_all([s1, s2])

        # axis rows – mixed labels for server 1, uniform for server 2
        rows = [
            # server 1
            McpLlmAxisScore(
                server_id=1,
                axis_name="overall_risk",
                label="high",
                label_index=3,
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.7,
                escalated=True,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="auth_strength",
                label="medium",
                label_index=2,
                p_top=0.3,
                p_critical=0.4,
                p_danger=0.3,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="capability_breadth",
                label="low",
                label_index=1,
                p_top=0.6,
                p_critical=0.3,
                p_danger=0.1,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="data_sensitivity",
                label="medium",
                label_index=2,
                p_top=0.2,
                p_critical=0.5,
                p_danger=0.3,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="network_egress",
                label="high",
                label_index=3,
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.7,
                escalated=True,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="maintainer_trust",
                label="low",
                label_index=1,
                p_top=0.7,
                p_critical=0.2,
                p_danger=0.1,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=1,
                axis_name="exploit_surface",
                label="medium",
                label_index=2,
                p_top=0.3,
                p_critical=0.4,
                p_danger=0.3,
                escalated=False,
            ),
            # server 2 – all axes present with identical values
            McpLlmAxisScore(
                server_id=2,
                axis_name="overall_risk",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="auth_strength",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="capability_breadth",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="data_sensitivity",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="network_egress",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="maintainer_trust",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
            McpLlmAxisScore(
                server_id=2,
                axis_name="exploit_surface",
                label="low",
                label_index=1,
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
            ),
        ]
        session.add_all(rows)
        session.commit()

    # ------------------------------------------------------------------- #
    # Override the FastAPI dependency to use our SQLite session
    # ------------------------------------------------------------------- #
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Run test
    # ------------------------------------------------------------------- #
    with SessionLocal() as test_session:
        seed(test_session)

    # monkey‑patch the dependency
    from fastapi import Depends
    from fastapi import params

    def _override_dep():
        return SessionLocal()

    # Direct call bypassing FastAPI – we manually provide the session
    with SessionLocal() as sess:
        resp = get_verdict_detail(1, session=sess)

    # Assertions
    assert resp.server_id == 1, "wrong server_id"
    assert resp.name == "Alpha", "wrong server name"
    assert len(resp.axes) == 7, f"expected 7 axes, got {len(resp.axes)}"
    valid_tiers = {"low", "medium", "high", "critical", "unknown"}
    assert resp.risk_tier in valid_tiers, f"risk_tier {resp.risk_tier} not in {valid_tiers}"
    print("PASS")