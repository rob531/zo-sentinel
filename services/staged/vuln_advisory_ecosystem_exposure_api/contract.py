"""Acceptance contract for the vulnerability advisory ecosystem exposure API."""

from __future__ import annotations

import importlib
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Table, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


def _module_name_for_path(path: Path) -> str:
    relative = path.resolve().relative_to(_REPOSITORY_ROOT).with_suffix("")
    return ".".join(relative.parts)


def _is_importable_source(path: Path) -> bool:
    excluded_directories = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "alembic",
        "migrations",
        "node_modules",
        "scripts",
        "staged",
        "tests",
        "venv",
    }
    relative_parts = path.relative_to(_REPOSITORY_ROOT).parts
    return not any(
        part in excluded_directories
        or (part.startswith("_") and part != "__init__.py")
        for part in relative_parts
    )


def _import_path(path: Path) -> Any:
    module_name = _module_name_for_path(path)
    return importlib.import_module(module_name)


def _tables_in_module(module: Any) -> dict[str, Table]:
    tables: dict[str, Table] = {}

    def add_value(value: Any) -> None:
        if isinstance(value, Table):
            tables.setdefault(value.name, value)
            return

        table_name = getattr(value, "__tablename__", None)
        if not isinstance(table_name, str):
            return

        table = getattr(value, "__table__", None)
        if isinstance(table, Table):
            tables.setdefault(table.name, table)

    for value in vars(module).values():
        add_value(value)
        if isinstance(value, type(importlib)):
            continue
        if hasattr(value, "__dict__") and getattr(value, "__name__", "").split(".")[0] in {
            "app",
            "models",
            "services",
        }:
            try:
                for nested_value in vars(value).values():
                    add_value(nested_value)
            except TypeError:
                pass

    return tables


def _find_live_tables() -> tuple[Table, Table]:
    candidates: list[Path] = []
    for path in _REPOSITORY_ROOT.rglob("*.py"):
        if not _is_importable_source(path):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if (
            "vuln_advisories" in source or "vuln_links" in source
        ) and any(
            marker in source
            for marker in ("__tablename__", "mapped_column", "Column(", "Table(")
        ):
            candidates.append(path)

    candidates.sort(
        key=lambda path: (
            0 if "model" in path.parts else 1,
            0 if path.stem.startswith("vuln") else 1,
            len(path.parts),
            str(path),
        )
    )

    discovered: dict[str, Table] = {}
    for path in candidates:
        try:
            module = _import_path(path)
        except Exception:
            continue
        for name, table in _tables_in_module(module).items():
            discovered.setdefault(name, table)
        if "vuln_advisories" in discovered and "vuln_links" in discovered:
            break

    if "vuln_advisories" not in discovered or "vuln_links" not in discovered:
        raise RuntimeError(
            "The real vulnerability advisory data model could not be imported."
        )

    return discovered["vuln_advisories"], discovered["vuln_links"]


_ADVISORY_TABLE, _LINK_TABLE = _find_live_tables()


def _find_data_layer() -> tuple[Callable[..., Iterator[Session]] | None, Engine | None, Any]:
    model_module = sys.modules.get(_module_name_for_path(_ADVISORY_TABLE.metadata.info.get("source", "") and Path(""))) if False else None

    references: list[Any] = []
    for module in list(sys.modules.values()):
        module_name = getattr(module, "__name__", "")
        if module_name.startswith("services.staged."):
            continue
        if not (
            module_name.startswith("app")
            or module_name.startswith("services")
            or module_name in {"database", "db"}
        ):
            continue
        references.append(module)

    dependency: Callable[..., Iterator[Session]] | None = None
    engine: Engine | None = None
    session_factory: Any = None

    for module in references:
        for name, value in vars(module).items():
            if dependency is None and name in {"get_db", "get_database", "get_session"}:
                if callable(value):
                    dependency = value
            if engine is None and isinstance(value, Engine):
                engine = value
            if session_factory is None and name in {"SessionLocal", "session_factory"}:
                if callable(value):
                    session_factory = value

    database_candidates: list[Path] = []
    for path in _REPOSITORY_ROOT.rglob("*.py"):
        if not _is_importable_source(path):
            continue
        if path.stem not in {"database", "db", "session"}:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(
            marker in source
            for marker in ("get_db", "SessionLocal", "create_engine", "sessionmaker")
        ):
            database_candidates.append(path)

    database_candidates.sort(key=lambda path: (len(path.parts), str(path)))
    for path in database_candidates:
        try:
            module = _import_path(path)
        except Exception:
            continue
        for name, value in vars(module).items():
            if dependency is None and name in {"get_db", "get_database", "get_session"}:
                if callable(value):
                    dependency = value
            if engine is None and isinstance(value, Engine):
                engine = value
            if session_factory is None and name in {"SessionLocal", "session_factory"}:
                if callable(value):
                    session_factory = value
        if dependency is not None or engine is not None or session_factory is not None:
            break

    return dependency, engine, session_factory


_REAL_GET_DB, _REAL_ENGINE, _SESSION_FACTORY = _find_data_layer()


def get_db() -> Iterator[Session]:
    if _REAL_GET_DB is not None:
        yield from _REAL_GET_DB()
        return

    if _SESSION_FACTORY is not None:
        with _SESSION_FACTORY() as session:
            yield session
        return

    if _REAL_ENGINE is None:
        raise RuntimeError("The real data layer does not expose a database session.")

    factory = sessionmaker(bind=_REAL_ENGINE, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session


class EcosystemExposure(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_server_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    max_published_at: str | None


class EcosystemExposureResponse(BaseModel):
    total_advisories: int
    total_links: int
    severity_unknown_count: int
    ecosystems: list[EcosystemExposure]


app = FastAPI(title="Vulnerability Advisory Ecosystem Exposure API")


def _as_iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    return str(value)


@app.get(
    "/api/vuln/ecosystems",
    response_model=EcosystemExposureResponse,
    tags=["vulnerability-advisories"],
)
def get_vuln_ecosystems(
    db: Session = Depends(get_db),
) -> EcosystemExposureResponse:
    totals = db.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT va.id) AS total_advisories,
                COUNT(vl.id) AS total_links,
                COUNT(DISTINCT CASE
                    WHEN LOWER(TRIM(COALESCE(va.severity, ''))) NOT IN
                         ('critical', 'high', 'medium', 'low')
                    THEN va.id
                END) AS severity_unknown_count
            FROM vuln_advisories AS va
            LEFT JOIN vuln_links AS vl ON vl.advisory_id = va.id
            """
        )
    ).mappings().one()

    rows = db.execute(
        text(
            """
            SELECT
                va.ecosystem AS ecosystem,
                COUNT(DISTINCT va.id) AS advisory_count,
                COUNT(DISTINCT vl.server_id) AS linked_server_count,
                COUNT(DISTINCT CASE
                    WHEN LOWER(TRIM(COALESCE(va.severity, ''))) = 'critical'
                    THEN va.id
                END) AS critical_count,
                COUNT(DISTINCT CASE
                    WHEN LOWER(TRIM(COALESCE(va.severity, ''))) = 'high'
                    THEN va.id
                END) AS high_count,
                COUNT(DISTINCT CASE
                    WHEN LOWER(TRIM(COALESCE(va.severity, ''))) = 'medium'
                    THEN va.id
                END) AS medium_count,
                COUNT(DISTINCT CASE
                    WHEN LOWER(TRIM(COALESCE(va.severity, ''))) = 'low'
                    THEN va.id
                END) AS low_count,
                MAX(va.published_at) AS max_published_at
            FROM vuln_advisories AS va
            LEFT JOIN vuln_links AS vl ON vl.advisory_id = va.id
            GROUP BY va.ecosystem
            ORDER BY advisory_count DESC, va.ecosystem ASC
            """
        )
    ).mappings()

    ecosystems = [
        EcosystemExposure(
            ecosystem=str(row["ecosystem"]),
            advisory_count=int(row["advisory_count"]),
            linked_server_count=int(row["linked_server_count"]),
            critical_count=int(row["critical_count"]),
            high_count=int(row["high_count"]),
            medium_count=int(row["medium_count"]),
            low_count=int(row["low_count"]),
            max_published_at=_as_iso_timestamp(row["max_published_at"]),
        )
        for row in rows
    ]

    return EcosystemExposureResponse(
        total_advisories=int(totals["total_advisories"]),
        total_links=int(totals["total_links"]),
        severity_unknown_count=int(totals["severity_unknown_count"]),
        ecosystems=ecosystems,
    )


def run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    _ADVISORY_TABLE.create(bind=test_engine)
    _LINK_TABLE.create(bind=test_engine)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    published_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with TestSession() as session:
        session.execute(
            _ADVISORY_TABLE.insert().values(
                id=1,
                feed="test-feed",
                summary="Critical advisory",
                severity="CRITICAL",
                ecosystem="alpha",
                package="example-alpha",
                affected_ranges="<2.0",
                aliases=None,
                source_url="https://example.invalid/advisories/1",
                published_at=published_at,
                fetched_at=published_at,
                identities=None,
                content_hash="hash-1",
            )
        )
        session.execute(
            _ADVISORY_TABLE.insert().values(
                id=2,
                feed="test-feed",
                summary="High advisory",
                severity="high",
                ecosystem="beta",
                package="example-beta",
                affected_ranges="<1.5",
                aliases=None,
                source_url="https://example.invalid/advisories/2",
                published_at=published_at,
                fetched_at=published_at,
                identities=None,
                content_hash="hash-2",
            )
        )
        session.execute(
            _ADVISORY_TABLE.insert().values(
                id=3,
                feed="test-feed",
                summary="Unknown-severity advisory",
                severity="UNKNOWN",
                ecosystem="gamma",
                package="example-gamma",
                affected_ranges="<3.0",
                aliases=None,
                source_url="https://example.invalid/advisories/3",
                published_at=published_at,
                fetched_at=published_at,
                identities=None,
                content_hash="hash-3",
            )
        )
        session.execute(
            _LINK_TABLE.insert().values(
                id=1,
                advisory_id=1,
                server_id=101,
                match_basis="package",
                match_value="example-alpha",
                match_confidence=1.0,
                linked_at=published_at,
            )
        )
        session.execute(
            _LINK_TABLE.insert().values(
                id=2,
                advisory_id=1,
                server_id=202,
                match_basis="package",
                match_value="example-alpha",
                match_confidence=0.9,
                linked_at=published_at,
            )
        )
        session.commit()

    def override_get_db() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/vuln/ecosystems")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["total_advisories"] == 3
            assert payload["total_links"] == 2
            assert payload["severity_unknown_count"] == 1
            assert len(payload["ecosystems"]) == 3

            linked_ecosystems = [
                ecosystem
                for ecosystem in payload["ecosystems"]
                if ecosystem["linked_server_count"] == 2
            ]
            assert len(linked_ecosystems) == 1
            assert linked_ecosystems[0]["critical_count"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()