from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef


router = APIRouter(prefix="/api")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _iso8601(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return _as_text(value)


def _url_host(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else "//" + value)
    hostname = parsed.hostname
    return hostname.rstrip(".").lower() if hostname else ""


def _normalized_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    hostname = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port
    scheme = parsed.scheme.lower()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname
    if port is not None and not default_port:
        netloc = "{}:{}".format(netloc, port)
    path = parsed.path or "/"
    return "{}://{}{}".format(scheme, netloc, path)


def _registry_match_basis(indicator: str, registry_url: str) -> Optional[str]:
    indicator = indicator.strip()
    registry_url = registry_url.strip()
    if not indicator or not registry_url:
        return None

    if _normalized_url(indicator) and _normalized_url(indicator) == _normalized_url(registry_url):
        return "url"

    indicator_host = _url_host(indicator)
    registry_host = _url_host(registry_url)
    if indicator_host and indicator_host == registry_host:
        return "host"

    return None


def build_associations(session: Session) -> Dict[str, Any]:
    references = session.query(ThreatIntelRef).order_by(ThreatIntelRef.id).all()
    servers = session.query(McpServerRegistry).all()

    server_rows = []
    for server in servers:
        server_id = _as_text(getattr(server, "server_id", ""))
        if not server_id:
            continue
        server_rows.append(
            (
                server_id,
                _as_text(getattr(server, "name", "")),
                _as_text(getattr(server, "risk_tier", "")),
                _as_text(getattr(server, "verdict", "")),
                _as_text(getattr(server, "url", "")),
            )
        )

    response_references: List[Dict[str, Any]] = []
    matched_reference_count = 0
    matched_servers: Set[str] = set()

    for reference in references:
        indicator_value = _as_text(getattr(reference, "indicator_value", ""))
        matches: List[Dict[str, Any]] = []

        for server_id, server_name, risk_tier, verdict, registry_url in server_rows:
            match_basis = _registry_match_basis(indicator_value, registry_url)
            if match_basis is None:
                continue
            matches.append(
                {
                    "server_id": server_id,
                    "server_name": server_name,
                    "risk_tier": risk_tier,
                    "verdict": verdict,
                    "match_basis": match_basis,
                }
            )
            matched_servers.add(server_id)

        if matches:
            matched_reference_count += 1

        response_references.append(
            {
                "indicator_type": _as_text(getattr(reference, "indicator_type", "")),
                "indicator_value": indicator_value,
                "pulse_id": _as_text(getattr(reference, "pulse_id", "")),
                "pulse_name": _as_text(getattr(reference, "pulse_name", "")),
                "source": _as_text(getattr(reference, "source", "")),
                "source_url": _as_text(getattr(reference, "source_url", "")),
                "fetched_at": _iso8601(getattr(reference, "fetched_at", None)),
                "is_aggregator": _as_bool(getattr(reference, "is_aggregator", False)),
                "matches": matches,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "references": response_references,
        "totals": {
            "references": len(response_references),
            "matched_references": matched_reference_count,
            "matched_servers": len(matched_servers),
        },
    }


@router.get("/threat_intel/associations")
def get_associations(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return build_associations(session)


def list_associations(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return build_associations(session)


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    ThreatIntelRef.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine)

    test_session = TestingSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="srv-1",
                name="Example Server",
                url="https://example.test/mcp",
                risk_tier="high",
                verdict="approved",
                trust_score=80,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Other Server",
                url="https://other.test/mcp",
                risk_tier="low",
                verdict="approved",
                trust_score=90,
            ),
            ThreatIntelRef(
                id=1,
                indicator_type="url",
                indicator_value="https://example.test/mcp",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                is_aggregator=False,
                source="test",
                source_url="https://intel.test/1",
                fetched_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="aggregator.test",
                pulse_id="pulse-2",
                pulse_name="Aggregator Pulse",
                pulse_created=datetime(2024, 1, 2, tzinfo=timezone.utc),
                is_aggregator=True,
                source="test",
                source_url="https://intel.test/2",
                fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="unmatched.test",
                pulse_id="pulse-3",
                pulse_name="Unmatched Pulse",
                pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                is_aggregator=False,
                source="test",
                source_url="https://intel.test/3",
                fetched_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
        ]
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    response = TestClient(that_app).get("/api/threat_intel/associations")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["references"]) == 3
    assert sum(bool(reference["matches"]) for reference in payload["references"]) == 1
    assert len(payload["references"][0]["matches"]) == 1
    assert payload["totals"]["matched_servers"] == 1
    print("PASS")