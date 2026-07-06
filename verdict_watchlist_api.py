# verdict_watchlist_api.py
from datetime import datetime
from typing import List, Dict, Any

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from app.db import get_session, Base
from app.models import ServerRegistry, LlmAxisScores  # type: ignore
import verdict_watchlist_service as vws

router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class WatchlistStatus(BaseModel):
    server_id: str
    is_watched: bool
    reason: str | None = None
    added_by: str | None = None
    added_at: datetime | None = None


class VerdictDetail(BaseModel):
    server_id: str
    axes: Dict[str, float]
    composite: float
    risk_tier: str
    is_watched: bool
    override_active: bool = Field(False, description="True when trust_gate forced TRUSTED_GENERAL")


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _risk_tier_from_composite(comp: float) -> str:
    if comp > 75:
        return "TRUSTED_GENERAL"
    if comp > 60:
        return "TRUSTED_RESEARCH"
    if comp > 45:
        return "ENTERPRISE_CONTROLLED"
    if comp > 30:
        return "CAUTION_LIMITED"
    if comp > 15:
        return "HIGH_RISK_ISOLATED"
    return "HIGH_RISK_ISOLATED"


def _fetch_watchlist_status(server_id: str) -> WatchlistStatus:
    data = vws.get_watchlist_status(server_id)
    return WatchlistStatus(
        server_id=server_id,
        is_watched=data.get("is_watched", False),
        reason=data.get("reason"),
        added_by=data.get("added_by"),
        added_at=data.get("added_at"),
    )


def _fetch_all_watchlist_statuses() -> List[WatchlistStatus]:
    raw = vws.get_all_watchlist_statuses()
    return [
        WatchlistStatus(
            server_id=item["server_id"],
            is_watched=item.get("is_watched", False),
            reason=item.get("reason"),
            added_by=item.get("added_by"),
            added_at=item.get("added_at"),
        )
        for item in raw
    ]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/watchlist",
    response_model=WatchlistStatus,
    responses={404: {"description": "Server not found"}},
)
def get_server_watchlist(
    server_id: str, session=Depends(get_session)
):
    if not session.query(ServerRegistry).filter_by(server_id=server_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return _fetch_watchlist_status(server_id)


@router.get(
    "/servers/{server_id}/verdict-detail",
    response_model=VerdictDetail,
    responses={404: {"description": "Server not found"}},
)
def get_server_verdict_detail(
    server_id: str, session=Depends(get_session)
):
    # verify server exists
    server = session.query(ServerRegistry).filter_by(server_id=server_id).first()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # fetch axis scores
    scores = session.query(LlmAxisScores).filter_by(server_id=server_id).first()
    if not scores:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Axis scores missing")

    # build axes dict (exclude id & server_id & composite)
    axes = {
        col.name: getattr(scores, col.name)
        for col in scores.__table__.columns
        if col.name not in {"id", "server_id", "composite"}
    }

    composite = getattr(scores, "composite", 0.0)
    risk_tier = _risk_tier_from_composite(composite)

    # watchlist status
    wl_status = vws.get_watchlist_status(server_id)
    is_watched = wl_status.get("is_watched", False)

    # trust gate override
    override_active = False
    try:
        tg_result = vws.trust_gating_override.trust_gate(server.url, server.name, axes)
        if tg_result == "trusted":
            risk_tier = "TRUSTED_GENERAL"
            override_active = True
    except Exception:
        # any failure just falls back to normal risk tier
        pass

    return VerdictDetail(
        server_id=server_id,
        axes=axes,
        composite=composite,
        risk_tier=risk_tier,
        is_watched=is_watched,
        override_active=override_active,
    )


@router.get(
    "/watchlist",
    response_model=List[WatchlistStatus],
)
def get_watchlist():
    return _fetch_all_watchlist_statuses()


# --------------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test only)
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    def _override_get_session():
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    sess = SessionLocal()
    # three servers
    servers = [
        ServerRegistry(server_id="srv1", url="http://srv1.example", name="normal_server"),
        ServerRegistry(server_id="srv2", url="http://srv2.example", name="override_server"),
        ServerRegistry(server_id="srv3", url="http://srv3.example", name="watched_server"),
    ]
    sess.add_all(servers)

    # axis scores
    scores = [
        LlmAxisScores(
            server_id="srv1",
            axis1=10,
            axis2=12,
            axis3=14,
            axis4=15,
            axis5=13,
            axis6=11,
            axis7=9,
            composite=82.0,
        ),
        LlmAxisScores(
            server_id="srv2",
            axis1=5,
            axis2=6,
            axis3=7,
            axis4=8,
            axis5=5,
            axis6=6,
            axis7=4,
            composite=40.0,
        ),
        LlmAxisScores(
            server_id="srv3",
            axis1=2,
            axis2=3,
            axis3=2,
            axis4=3,
            axis5=2,
            axis6=3,
            axis7=2,
            composite=20.0,
        ),
    ]
    sess.add_all(scores)
    sess.commit()
    sess.close()

    # ------------------------------------------------------------------- #
    # Mock verdict_watchlist_service behaviour
    # ------------------------------------------------------------------- #
    from datetime import timezone

    _watchlist_store = {
        "srv1": {"is_watched": False, "reason": None, "added_by": None, "added_at": None},
        "srv2": {"is_watched": False, "reason": None, "added_by": None, "added_at": None},
        "srv3": {
            "is_watched": True,
            "reason": "policy",
            "added_by": "admin",
            "added_at": datetime.now(tz=timezone.utc),
        },
    }

    def _mock_get_watchlist_status(sid: str) -> Dict[str, Any]:
        return _watchlist_store.get(sid, {"is_watched": False, "reason": None, "added_by": None, "added_at": None})

    def _mock_get_all_watchlist_statuses() -> List[Dict[str, Any]]:
        return [
            {"server_id": sid, **info}
            for sid, info in _watchlist_store.items()
            if info.get("is_watched")
        ]

    class _MockTrustGate:
        @staticmethod
        def trust_gate(url: str, name: str, axes: Dict[str, float]) -> str:
            return "trusted" if name == "override_server" else "untrusted"

    vws.get_watchlist_status = _mock_get_watchlist_status  # type: ignore
    vws.get_all_watchlist_statuses = _mock_get_all_watchlist_statuses  # type: ignore
    vws.trust_gating_override = _MockTrustGate()  # type: ignore

    # ------------------------------------------------------------------- #
    # FastAPI app creation
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # dependency override for the test DB
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Tests
    # ------------------------------------------------------------------- #
    try:
        # srv1 – normal composite >75 => TRUSTED_GENERAL, no override, not watched
        r = client.get("/servers/srv1/verdict-detail")
        assert r.status_code == 200
        d = r.json()
        assert d["risk_tier"] == "TRUSTED_GENERAL"
        assert d["is_watched"] is False
        assert d["override_active"] is False

        # srv2 – composite 40 would be ENTERPRISE_CONTROLLED but override forces TRUSTED_GENERAL
        r = client.get("/servers/srv2/verdict-detail")
        assert r.status_code == 200
        d = r.json()
        assert d["risk_tier"] == "TRUSTED_GENERAL"
        assert d["override_active"] is True

        # srv3 – watched server, composite 20 => HIGH_RISK_ISOLATED, watched flag true
        r = client.get("/servers/srv3/verdict-detail")
        assert r.status_code == 200
        d = r.json()
        assert d["risk_tier"] == "HIGH_RISK_ISOLATED"
        assert d["is_watched"] is True
        assert d["override_active"] is False

        # watchlist endpoint returns only the watched server
        r = client.get("/watchlist")
        assert r.status_code == 200
        lst = r.json()
        assert isinstance(lst, list) and len(lst) == 1
        assert lst[0]["server_id"] == "srv3"
        assert lst[0]["is_watched"] is True

        # unknown server -> 404
        r = client.get("/servers/unknown/verdict-detail")
        assert r.status_code == 404

        print("PASS")
    except AssertionError:
        print("FAIL", file=sys.stderr)
        sys.exit(1)