from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


_SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def _normalise_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalised = value.strip().lower()
    return normalised or None


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def get_vuln_advisory_ecosystem_severity(
    db: Optional[Session] = None,
    ecosystem: Optional[str] = None,
    severity: Optional[str] = None,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    active_session = session if session is not None else db
    if active_session is None:
        raise ValueError("A database session is required")

    ecosystem_filter = _normalise_optional(ecosystem)
    severity_filter = _normalise_optional(severity)

    ecosystem_column = func.coalesce(
        func.nullif(func.trim(VulnAdvisory.ecosystem), ""),
        "unknown",
    )
    severity_column = func.coalesce(
        func.nullif(func.lower(func.trim(VulnAdvisory.severity)), ""),
        "unknown",
    )

    statement = select(
        ecosystem_column.label("ecosystem"),
        func.count().label("total"),
        func.sum(
            case((severity_column == "critical", 1), else_=0)
        ).label("critical"),
        func.sum(
            case((severity_column == "high", 1), else_=0)
        ).label("high"),
        func.sum(
            case((severity_column == "medium", 1), else_=0)
        ).label("medium"),
        func.sum(
            case((severity_column == "low", 1), else_=0)
        ).label("low"),
        func.sum(
            case((severity_column == "unknown", 1), else_=0)
        ).label("unknown"),
        func.max(VulnAdvisory.published_at).label("latest_published_at"),
    )

    if ecosystem_filter is not None:
        statement = statement.where(
            func.lower(func.trim(VulnAdvisory.ecosystem))
            == ecosystem_filter
        )
    if severity_filter is not None:
        statement = statement.where(severity_column == severity_filter)

    statement = statement.group_by(ecosystem_column).order_by(
        func.count().desc(),
        ecosystem_column.asc(),
    )

    rows = active_session.execute(statement).mappings().all()

    ecosystems: List[Dict[str, Any]] = []
    for row in rows:
        ecosystems.append(
            {
                "ecosystem": row["ecosystem"],
                "total": int(row["total"] or 0),
                "by_severity": {
                    severity: int(row[severity] or 0)
                    for severity in _SEVERITIES
                },
                "latest_published_at": _isoformat(row["latest_published_at"]),
            }
        )

    return {
        "ecosystems": ecosystems,
        "total_advisories": sum(item["total"] for item in ecosystems),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ecosystem_severity(
    db: Optional[Session] = None,
    ecosystem: Optional[str] = None,
    severity: Optional[str] = None,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return get_vuln_advisory_ecosystem_severity(
        db,
        ecosystem,
        severity,
        session=session,
    )


def get_ecosystem_severity_distribution(
    db: Optional[Session] = None,
    ecosystem: Optional[str] = None,
    severity: Optional[str] = None,
    *,
    session: Optional[Session] = None,
) -> Dict[str, Any]:
    return get_vuln_advisory_ecosystem_severity(
        db,
        ecosystem,
        severity,
        session=session,
    )


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    advisories = [
        VulnAdvisory(
            id="self-test-npm-critical",
            feed="contract",
            summary="npm critical advisory",
            severity="critical",
            ecosystem="npm",
            package="critical-package",
            published_at=now,
            fetched_at=now,
            content_hash="self-test-npm-critical",
            source_url="https://example.invalid/npm-critical",
            affected_ranges=[],
            aliases=[],
            identities={},
        ),
        VulnAdvisory(
            id="self-test-npm-high",
            feed="contract",
            summary="npm high advisory",
            severity="high",
            ecosystem="npm",
            package="high-package",
            published_at=now,
            fetched_at=now,
            content_hash="self-test-npm-high",
            source_url="https://example.invalid/npm-high",
            affected_ranges=[],
            aliases=[],
            identities={},
        ),
        VulnAdvisory(
            id="self-test-pypi-medium",
            feed="contract",
            summary="pypi medium advisory",
            severity="medium",
            ecosystem="pypi",
            package="medium-package",
            published_at=now,
            fetched_at=now,
            content_hash="self-test-pypi-medium",
            source_url="https://example.invalid/pypi-medium",
            affected_ranges=[],
            aliases=[],
            identities={},
        ),
        VulnAdvisory(
            id="self-test-unknown",
            feed="contract",
            summary="unknown severity advisory",
            severity=None,
            ecosystem="other",
            package="unknown-package",
            published_at=now,
            fetched_at=now,
            content_hash="self-test-unknown",
            source_url="https://example.invalid/unknown",
            affected_ranges=[],
            aliases=[],
            identities={},
        ),
    ]

    with test_session_factory() as db:
        db.add_all(advisories)
        db.commit()

        test_app = FastAPI()

        @test_app.get("/api/vuln/advisories/ecosystem-severity")
        def ecosystem_severity_route(
            ecosystem: Optional[str] = None,
            severity: Optional[str] = None,
            db: Session = Depends(get_session),
        ) -> Dict[str, Any]:
            return get_vuln_advisory_ecosystem_severity(
                db,
                ecosystem,
                severity,
            )

        def override_get_session():
            yield db

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/vuln/advisories/ecosystem-severity",
                params={"ecosystem": "NPM"},
            )
            assert response.status_code == 200
            payload = response.json()

            npm = next(
                item
                for item in payload["ecosystems"]
                if item["ecosystem"] == "npm"
            )
            assert npm["total"] == 2
            assert npm["by_severity"]["high"] == 1
            assert npm["by_severity"]["critical"] == 1
            assert payload["total_advisories"] == 2

            response = client.get(
                "/api/vuln/advisories/ecosystem-severity"
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_advisories"] == 4
            assert sum(
                item["by_severity"]["unknown"]
                for item in payload["ecosystems"]
            ) == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()