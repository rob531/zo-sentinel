from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


__all__ = ["build_coverage_report"]


def _app_session(session: Any = Depends(get_session)) -> Any:
    return session


def _resolve_session(value: Any) -> tuple[Any, Any]:
    if hasattr(value, "execute"):
        return value, None

    if not callable(value):
        raise TypeError("db_session_or_callable must be a session or callable")

    resource = value()

    if hasattr(resource, "execute"):
        return resource, ("close", resource)

    if hasattr(resource, "__next__"):
        session = next(resource)
        return session, ("close_generator", resource)

    if isinstance(resource, AbstractContextManager):
        session = resource.__enter__()
        return session, ("exit_context", resource)

    if hasattr(resource, "__enter__") and hasattr(resource, "__exit__"):
        session = resource.__enter__()
        return session, ("exit_context", resource)

    raise TypeError("The supplied callable did not return a database session")


def _cleanup_session(session: Any, cleanup: Any) -> None:
    if cleanup is None:
        return

    kind, resource = cleanup
    if kind == "close":
        close = getattr(resource, "close", None)
        if close is not None:
            close()
    elif kind == "close_generator":
        close = getattr(resource, "close", None)
        if close is not None:
            close()
    elif kind == "exit_context":
        resource.__exit__(None, None, None)


def build_coverage_report(db_session_or_callable: Any) -> dict:
    session, cleanup = _resolve_session(db_session_or_callable)

    try:
        registry_rows = session.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.registry_source,
            )
        ).all()

        corpus_rows = session.execute(
            select(AskCorpusDoc.server_id).distinct()
        ).all()

        indexed_server_ids = {row[0] for row in corpus_rows if row[0] is not None}

        total_servers = len(registry_rows)
        uncovered: list[tuple[Any, str]] = []
        covered_servers = 0

        for server_id, registry_source in registry_rows:
            if server_id in indexed_server_ids:
                covered_servers += 1
                continue

            family = str(registry_source).strip() if registry_source is not None else ""
            if not family:
                family = "unknown"
            uncovered.append((server_id, family))

        family_counts: dict[str, int] = {}
        for _, family in uncovered:
            family_counts[family] = family_counts.get(family, 0) + 1

        ranked_families = sorted(
            family_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )

        uncovered_sample = sorted(
            (server_id for server_id, _ in uncovered),
            key=lambda value: str(value),
        )[:50]

        coverage_pct = (
            round((covered_servers / total_servers) * 100.0, 2)
            if total_servers
            else 0.0
        )

        return {
            "total_servers": total_servers,
            "covered_servers": covered_servers,
            "coverage_pct": coverage_pct,
            "uncovered_sample": uncovered_sample,
            "uncovered_count": len(uncovered),
            "top_uncovered_families": dict(ranked_families[:10]),
        }
    finally:
        _cleanup_session(session, cleanup)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    McpServerRegistry.__table__.create(engine, checkfirst=True)
    AskCorpusDoc.__table__.create(engine, checkfirst=True)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="alpha",
                    name="Alpha",
                    url="https://example.test/alpha",
                    registry_source="npm",
                ),
                McpServerRegistry(
                    server_id="beta",
                    name="Beta",
                    url="https://example.test/beta",
                    registry_source="github",
                ),
                McpServerRegistry(
                    server_id="gamma",
                    name="Gamma",
                    url="https://example.test/gamma",
                    registry_source="npm",
                ),
                McpServerRegistry(
                    server_id="delta",
                    name="Delta",
                    url="https://example.test/delta",
                    registry_source="pypi",
                ),
                McpServerRegistry(
                    server_id="epsilon",
                    name="Epsilon",
                    url="https://example.test/epsilon",
                    registry_source="npm",
                ),
                AskCorpusDoc(
                    server_id="alpha",
                    content_hash="hash-alpha",
                ),
                AskCorpusDoc(
                    server_id="beta",
                    content_hash="hash-beta",
                ),
            ]
        )
        session.commit()

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = lambda: session

        report = build_coverage_report(session)

        assert report["total_servers"] == 5
        assert report["covered_servers"] == 2
        assert report["coverage_pct"] == 40.0
        assert report["uncovered_count"] == 3
        assert len(report["uncovered_sample"]) == 3
    finally:
        session.close()
        engine.dispose()

    print(
        "Coverage: 2/5 servers (40.0%), uncovered: 3"
    )
    print("PASS")


if __name__ == "__main__":
    _self_test()