"""
server_trust_gate router -- thin APIRouter exposing trust check endpoint.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["trust"])


class AxisScoreItem(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    p_top: Optional[float] = None


class TrustCheckResponse(BaseModel):
    url: str
    name: str
    is_trusted: bool
    overridden_verdict: Optional[str] = None
    axis_scores: list[AxisScoreItem]
    computed_at: datetime


def trust_gate(url: str, name: str, axis_labels: dict[str, str]) -> tuple[bool, Optional[str]]:
    """
    Compute trust gate verdict.
    Returns (is_trusted, overridden_verdict).
    A server is trusted if all axis labels are 'safe' or 'trusted'.
    """
    safe_labels = {"safe", "trusted"}
    overrides = {"bypass_trust": True, "force_trusted": True, "force_untrusted": False}
    for axis_name, label in axis_labels.items():
        if label in overrides:
            return overrides[label], f"override:{axis_name}={label}"
    all_safe = all(label.lower() in safe_labels for label in axis_labels.values())
    return all_safe, None


@router.get("/trust/check", response_model=TrustCheckResponse)
def trust_check(
    url: Annotated[str, Query(description="Server URL")],
    name: Annotated[str, Query(description="Server name")],
    session: Session = Depends(get_session),
) -> TrustCheckResponse:
    sql = text("""
        SELECT
            s.server_id,
            s.url,
            s.name,
            s.verdict,
            a.axis_name,
            a.label,
            a.label_index,
            a.p_critical,
            a.p_danger,
            a.p_top
        FROM mcp_server_registry s
        JOIN mcp_llm_axis_scores a ON s.server_id = a.server_id
        WHERE (s.url = :url OR s.name = :name)
          AND a.scored_at = (
              SELECT MAX(a2.scored_at)
              FROM mcp_llm_axis_scores a2
              WHERE a2.server_id = s.server_id
          )
        ORDER BY s.server_id, a.axis_name
    """)
    rows = session.execute(sql, {"url": url, "name": name}).fetchall()

    if not rows:
        return TrustCheckResponse(
            url=url,
            name=name,
            is_trusted=False,
            overridden_verdict=None,
            axis_scores=[],
            computed_at=datetime.now(timezone.utc),
        )

    axis_scores: list[AxisScoreItem] = []
    axis_labels: dict[str, str] = {}
    server_id = None
    for row in rows:
        if server_id is None:
            server_id = row.server_id
        axis_scores.append(AxisScoreItem(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            p_top=row.p_top,
        ))
        axis_labels[row.axis_name] = row.label

    is_trusted, overridden_verdict = trust_gate(url, name, axis_labels)

    return TrustCheckResponse(
        url=url,
        name=name,
        is_trusted=is_trusted,
        overridden_verdict=overridden_verdict,
        axis_scores=axis_scores,
        computed_at=datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                registry_source TEXT,
                description TEXT,
                risk_tier TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                trust_score REAL,
                confidence REAL,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT NOT NULL,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT,
                scored_at TEXT,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_signal_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                signal_name TEXT NOT NULL,
                score REAL,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))
        conn.commit()

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, url, name, verdict, trust_score)
            VALUES (1, 'https://trusted.example.com', 'trusted-server', 'safe', 0.95)
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, url, name, verdict, trust_score)
            VALUES (2, 'https://untrusted.example.com', 'untrusted-server', 'danger', 0.2)
        """))
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
            (server_id, axis_name, label, label_index, p_critical, p_danger, p_top, scored_at)
            VALUES (1, 'safety', 'safe', 0, 0.01, 0.05, 0.94, :now)
        """), {"now": now})
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
            (server_id, axis_name, label, label_index, p_critical, p_danger, p_top, scored_at)
            VALUES (1, 'reliability', 'trusted', 1, 0.02, 0.03, 0.95, :now)
        """), {"now": now})
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
            (server_id, axis_name, label, label_index, p_critical, p_danger, p_top, scored_at)
            VALUES (2, 'safety', 'danger', 1, 0.55, 0.30, 0.15, :now)
        """), {"now": now})
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
            (server_id, axis_name, label, label_index, p_critical, p_danger, p_top, scored_at)
            VALUES (2, 'reliability', 'risky', 2, 0.20, 0.45, 0.35, :now)
        """), {"now": now})
        conn.commit()

    TestingSessionLocal = sessionmaker(bind=engine)

    def _get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient

    that_app = app
    that_app.dependency_overrides[get_session] = _get_session
    client = TestClient(that_app)

    resp = client.get("/api/trust/check?url=https://trusted.example.com&name=trusted-server")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data["is_trusted"], bool), f"is_trusted must be bool: {data}"
    assert data["is_trusted"] is True, f"Trusted server expected is_trusted=True: {data}"

    resp2 = client.get("/api/trust/check?url=https://untrusted.example.com&name=untrusted-server")
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
    data2 = resp2.json()
    assert isinstance(data2["is_trusted"], bool), f"is_trusted must be bool: {data2}"
    assert data2["is_trusted"] is False, f"Untrusted server expected is_trusted=False: {data2}"

    print("PASS")