from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import and_, case, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class StrictModel(BaseModel):
    class Config:
        strict = True


class SourceCrosstab(StrictModel):
    registry_source: str
    total: int
    verdicts: Dict[str, int]
    risk_tiers: Dict[str, int]
    avg_confidence: Optional[float]
    scanned_pct: float


class SourceVerdictCrosstabResponse(StrictModel):
    sources: List[SourceCrosstab]


that_app = FastAPI()


@that_app.get(
    "/api/registry/source-verdict-crosstab",
    response_model=SourceVerdictCrosstabResponse,
)
def source_verdict_crosstab(session: Session = Depends(get_session)):
    registry = McpServerRegistry.__table__.c

    source_expr = func.coalesce(registry.registry_source, "UNKNOWN")
    known_verdict = and_(
        registry.verdict.is_not(None),
        func.trim(registry.verdict) != "",
    )
    verdict_expr = case(
        (known_verdict, func.trim(registry.verdict)),
        else_="UNKNOWN",
    )
    known_risk_tier = and_(
        registry.risk_tier.is_not(None),
        func.trim(registry.risk_tier) != "",
    )
    risk_tier_expr = case(
        (known_risk_tier, func.trim(registry.risk_tier)),
        else_="UNKNOWN",
    )
    scanned_expr = case(
        (registry.last_scanned.is_not(None), 1),
        else_=0,
    )

    stats_rows = session.execute(
        select(
            source_expr.label("registry_source"),
            func.count().label("total"),
            func.avg(registry.confidence).label("avg_confidence"),
            func.sum(scanned_expr).label("scanned"),
        )
        .group_by(source_expr)
        .order_by(func.count().desc(), source_expr.asc())
    ).all()

    verdict_rows = session.execute(
        select(
            source_expr.label("registry_source"),
            verdict_expr.label("verdict"),
            func.count().label("count"),
        )
        .group_by(source_expr, verdict_expr)
        .order_by(source_expr.asc(), verdict_expr.asc())
    ).all()

    risk_tier_rows = session.execute(
        select(
            source_expr.label("registry_source"),
            risk_tier_expr.label("risk_tier"),
            func.count().label("count"),
        )
        .group_by(source_expr, risk_tier_expr)
        .order_by(source_expr.asc(), risk_tier_expr.asc())
    ).all()

    verdicts_by_source: Dict[str, Dict[str, int]] = defaultdict(dict)
    for row in verdict_rows:
        verdicts_by_source[row.registry_source][row.verdict] = int(row.count)

    risk_tiers_by_source: Dict[str, Dict[str, int]] = defaultdict(dict)
    for row in risk_tier_rows:
        risk_tiers_by_source[row.registry_source][row.risk_tier] = int(row.count)

    sources = []
    for row in stats_rows:
        total = int(row.total)
        avg_confidence = (
            float(row.avg_confidence)
            if row.avg_confidence is not None
            else None
        )
        scanned_pct = round((int(row.scanned or 0) * 100.0) / total, 2)

        sources.append(
            SourceCrosstab(
                registry_source=str(row.registry_source),
                total=total,
                verdicts=dict(verdicts_by_source[row.registry_source]),
                risk_tiers=dict(risk_tiers_by_source[row.registry_source]),
                avg_confidence=avg_confidence,
                scanned_pct=scanned_pct,
            )
        )

    return SourceVerdictCrosstabResponse(sources=sources)


def _seed_database(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                registry_source VARCHAR NOT NULL,
                verdict VARCHAR NULL,
                risk_tier VARCHAR NULL,
                confidence FLOAT NULL,
                last_scanned DATETIME NULL,
                last_assessed DATETIME NULL,
                first_seen DATETIME NULL
            )
            """
        )
    )

    rows = [
        {
            "registry_source": "alpha",
            "verdict": "PASS",
            "risk_tier": "LOW",
            "confidence": 0.9,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "alpha",
            "verdict": "FAIL",
            "risk_tier": "HIGH",
            "confidence": 0.4,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "beta",
            "verdict": "PASS",
            "risk_tier": "MEDIUM",
            "confidence": 0.7,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "beta",
            "verdict": "FAIL",
            "risk_tier": "HIGH",
            "confidence": 0.3,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "gamma",
            "verdict": "PASS",
            "risk_tier": "MEDIUM",
            "confidence": 0.8,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "gamma",
            "verdict": "FAIL",
            "risk_tier": "LOW",
            "confidence": 0.6,
            "last_scanned": "2025-01-01T00:00:00",
        },
        {
            "registry_source": "gamma",
            "verdict": None,
            "risk_tier": "MEDIUM",
            "confidence": 0.5,
            "last_scanned": "2025-01-01T00:00:00",
        },
    ]
    session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (
                registry_source,
                verdict,
                risk_tier,
                confidence,
                last_scanned
            ) VALUES (
                :registry_source,
                :verdict,
                :risk_tier,
                :confidence,
                :last_scanned
            )
            """
        ),
        rows,
    )
    session.commit()


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with test_session_factory() as session:
        _seed_database(session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-verdict-crosstab")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["sources"]) == 3, payload

        sources = {item["registry_source"]: item for item in payload["sources"]}
        assert set(sources) == {"alpha", "beta", "gamma"}, payload

        gamma = sources["gamma"]
        assert gamma["total"] == 3, gamma
        assert gamma["verdicts"]["UNKNOWN"] == 1, gamma
        assert gamma["verdicts"]["PASS"] == 1, gamma
        assert gamma["verdicts"]["FAIL"] == 1, gamma
        assert sum(gamma["verdicts"].values()) == gamma["total"], gamma

        alpha = sources["alpha"]
        assert alpha["total"] == 2, alpha
        assert alpha["verdicts"]["PASS"] == 1, alpha

    print("PASS")