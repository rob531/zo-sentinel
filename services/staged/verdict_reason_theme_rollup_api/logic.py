"""services/staged/verdict_reason_theme_rollup_api/logic.py

Data/computation layer for the verdict_reason_theme_rollup_api service.
Provides a function that aggregates verdict reasoning themes across servers.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry

_THEMES: List[str] = [
    "auth",
    "egress",
    "injection",
    "supply_chain",
    "unmaintained",
    "no_scoring",
    "other",
]


def verdict_reason_theme_rollup(db: Session) -> Dict:
    """
    Aggregate verdict reasoning themes.

    Returns a dict:
    {
        "total_servers": int,          # servers with non‑null verdict_reasoning
        "themed_count": int,           # distinct servers that matched any theme
        "themes": [
            {"theme": str, "count": int, "share": float},
            ...
        ],
    }
    """
    # Pull needed columns
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict_reasoning,
        )
    ).all()

    total_servers = 0
    themed_servers = set()
    theme_counts: Dict[str, int] = {t: 0 for t in _THEMES}

    for server_id, reasoning in rows:
        if reasoning is None:
            continue
        total_servers += 1
        lowered = reasoning.lower()
        matched = False
        for theme in _THEMES:
            if theme in lowered:
                theme_counts[theme] += 1
                matched = True
        if matched:
            themed_servers.add(server_id)

    themed_count = len(themed_servers)

    # Build sorted theme list
    themes_list = [
        {
            "theme": theme,
            "count": count,
            "share": count / total_servers if total_servers else 0.0,
        }
        for theme, count in theme_counts.items()
        if count > 0
    ]
    themes_list.sort(key=lambda x: (-x["count"], x["theme"]))

    return {
        "total_servers": total_servers,
        "themed_count": themed_count,
        "themes": themes_list,
    }


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session as original_get_session

    # --------------------------------------------------------------------- #
    # Test DB setup
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Seed data
    # --------------------------------------------------------------------- #
    with TestingSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="s1",
                verdict="pass",
                verdict_reasoning="Auth issue detected",
            ),
            McpServerRegistry(
                server_id="s2",
                verdict="pass",
                verdict_reasoning="Egress traffic observed",
            ),
            McpServerRegistry(
                server_id="s3",
                verdict="pass",
                verdict_reasoning=None,
            ),
            McpServerRegistry(
                server_id="s4",
                verdict="pass",
                verdict_reasoning="Unrelated reasoning",
            ),
        ]
        db.add_all(servers)
        db.commit()

    # --------------------------------------------------------------------- #
    # FastAPI app wiring
    # --------------------------------------------------------------------- #
    app = FastAPI()

    @app.get("/api/verdict/reason-themes")
    def endpoint(db: Session = Depends(get_test_session)):
        return verdict_reason_theme_rollup(db)

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute test
    # --------------------------------------------------------------------- #
    resp = client.get("/api/verdict/reason-themes")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # total servers with reasoning = 3 (s1, s2, s4)
    assert data["total_servers"] == 3
    # themed servers = 2 (s1 matches auth, s2 matches egress)
    assert data["themed_count"] == 2

    # Find auth theme entry
    auth_entry = next((t for t in data["themes"] if t["theme"] == "auth"), None)
    assert auth_entry is not None, "auth theme missing"
    assert auth_entry["count"] == 1, f"auth count {auth_entry['count']} != 1"

    # Ensure null‑reasoning server not counted
    null_server_id = "s3"
    # No theme should include s3; we already ensured themed_count == 2

    print("PASS")