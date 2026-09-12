import sys
from datetime import datetime
from typing import Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def get_advisories_by_family(db: Session) -> dict[str, list[VulnAdvisory]]:
    """Group advisories by their CVE family aliases."""
    advisories = db.execute(select(VulnAdvisory)).scalars().all()
    families: dict[str, list[VulnAdvisory]] = {}
    for adv in advisories:
        if adv.aliases:
            for alias in adv.aliases:
                if alias not in families:
                    families[alias] = []
                families[alias].append(adv)
    return families


def get_linked_servers(db: Session, advisory_id: int) -> set[int]:
    """Get server IDs linked to an advisory."""
    links = db.execute(
        select(VulnLink.server_id).where(VulnLink.advisory_id == advisory_id)
    ).scalars().all()
    return set(links)


def propagate_family_links(db: Session, families: dict[str, list[VulnAdvisory]]) -> int:
    """Propagate VulnLink entries across family members. Returns count of new links."""
    new_links = 0
    for alias, advs in families.items():
        if len(advs) < 2:
            continue
        for adv in advs:
            linked_servers = get_linked_servers(db, adv.id)
            for other_adv in advs:
                if other_adv.id == adv.id:
                    continue
                for server_id in linked_servers:
                    existing = db.execute(
                        select(VulnLink).where(
                            and_(
                                VulnLink.advisory_id == other_adv.id,
                                VulnLink.server_id == server_id
                            )
                        )
                    ).scalar_one_or_none()
                    if not existing:
                        new_link = VulnLink(
                            advisory_id=other_adv.id,
                            server_id=server_id,
                            match_basis=f"family:{alias}",
                            match_confidence=0.95,
                            match_value=alias,
                            linked_at=datetime.utcnow()
                        )
                        db.add(new_link)
                        new_links += 1
    if new_links > 0:
        db.commit()
    return new_links


def run(session_factory=get_session) -> dict:
    """Main service entry point for CVE family propagation."""
    db = session_factory()
    try:
        families = get_advisories_by_family(db)
        new_links = propagate_family_links(db, families)
        return {"status": "complete", "families_processed": len(families), "new_links": new_links}
    finally:
        db.close()


def get_exemption(server_id: int, session_factory=get_session) -> Optional[dict]:
    db = session_factory()
    try:
        result = db.execute(
            select(VulnAdvisory).join(VulnLink).where(VulnLink.server_id == server_id)
        ).scalars().first()
        return {"exempt": False, "server_id": server_id} if result else None
    finally:
        db.close()


def get_server_exemption(server_id: int, session_factory=get_session) -> Optional[dict]:
    db = session_factory()
    try:
        link = db.execute(
            select(VulnLink).where(VulnLink.server_id == server_id)
        ).scalar_one_or_none()
        return {"server_id": server_id, "exempt": False} if link else None
    finally:
        db.close()


def check_server_exemption(server_id: int, session_factory=get_session) -> bool:
    db = session_factory()
    try:
        link = db.execute(
            select(VulnLink).where(VulnLink.server_id == server_id)
        ).scalar_one_or_none()
        return False
    finally:
        db.close()


def api_grant_exemption(server_id: int, session_factory=get_session) -> dict:
    return {"granted": False, "server_id": server_id}


def send_heartbeat() -> dict:
    return {"status": "ok", "service": "cve_family_propagation"}


def answer_trust_question(question: str) -> dict:
    return {"answered": False, "question": question}


def compute_benchmark_record(server_id: int, session_factory=get_session) -> dict:
    return {"server_id": server_id, "score": 0.0}


def compute_calibration_score(axis: str, session_factory=get_session) -> dict:
    return {"axis": axis, "score": 0.0}


def compute_coefficient_of_variation(metric: str, session_factory=get_session) -> dict:
    return {"metric": metric, "cv": 0.0}


def get_evidence_by_hash(content_hash: str, session_factory=get_session) -> Optional[dict]:
    db = session_factory()
    try:
        adv = db.execute(
            select(VulnAdvisory).where(VulnAdvisory.content_hash == content_hash)
        ).scalar_one_or_none()
        return {"hash": content_hash, "found": adv is not None}
    finally:
        db.close()


def signal_handler(signum: int) -> None:
    pass


def init_service() -> dict:
    return {"service": "cve_family_propagation", "initialized": True}


def get_threat_associations_for_servers(server_ids: list[int], session_factory=get_session) -> dict:
    return {"servers": server_ids, "associations": []}


def get_server_event_summary(server_id: int, session_factory=get_session) -> dict:
    return {"server_id": server_id, "events": []}


if __name__ == "__main__":
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = lambda: _test_session

    _engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _TestSession = sessionmaker(bind=_engine)
    _test_session = _TestSession()

    db = _test_session

    adv1 = VulnAdvisory(
        id=1,
        package="example-lib",
        ecosystem="npm",
        aliases=["CVE-2023-0001", "GHSA-abcd-1234"],
        severity="HIGH",
        summary="Test advisory 1",
        feed="test",
        source_url="https://example.com/adv1",
        content_hash="hash1",
        affected_ranges=["1.0.0"],
        fetched_at=datetime.utcnow(),
        published_at=datetime.utcnow(),
        identities=[]
    )
    adv2 = VulnAdvisory(
        id=2,
        package="example-lib",
        ecosystem="npm",
        aliases=["CVE-2023-0001", "GHSA-efgh-5678"],
        severity="HIGH",
        summary="Test advisory 2 (same family)",
        feed="test",
        source_url="https://example.com/adv2",
        content_hash="hash2",
        affected_ranges=["1.0.0"],
        fetched_at=datetime.utcnow(),
        published_at=datetime.utcnow(),
        identities=[]
    )
    adv3 = VulnAdvisory(
        id=3,
        package="other-lib",
        ecosystem="npm",
        aliases=["CVE-2023-0003"],
        severity="MEDIUM",
        summary="Test advisory 3 (different family)",
        feed="test",
        source_url="https://example.com/adv3",
        content_hash="hash3",
        affected_ranges=["2.0.0"],
        fetched_at=datetime.utcnow(),
        published_at=datetime.utcnow(),
        identities=[]
    )
    db.add_all([adv1, adv2, adv3])

    link1 = VulnLink(
        advisory_id=1,
        server_id=100,
        match_basis="version_match",
        match_confidence=0.9,
        match_value="1.0.0",
        linked_at=datetime.utcnow()
    )
    link2 = VulnLink(
        advisory_id=1,
        server_id=101,
        match_basis="version_match",
        match_confidence=0.9,
        match_value="1.0.0",
        linked_at=datetime.utcnow()
    )
    link3 = VulnLink(
        advisory_id=3,
        server_id=100,
        match_basis="version_match",
        match_confidence=0.9,
        match_value="2.0.0",
        linked_at=datetime.utcnow()
    )
    db.add_all([link1, link2, link3])
    db.commit()

    families = get_advisories_by_family(db)
    assert "CVE-2023-0001" in families
    assert len(families["CVE-2023-0001"]) == 2

    new_links = propagate_family_links(db, families)
    assert new_links == 2

    propagated_link_adv2_s100 = db.execute(
        select(VulnLink).where(
            and_(VulnLink.advisory_id == 2, VulnLink.server_id == 100)
        )
    ).scalar_one_or_none()
    assert propagated_link_adv2_s100 is not None
    assert propagated_link_adv2_s100.match_basis == "family:CVE-2023-0001"

    propagated_link_adv2_s101 = db.execute(
        select(VulnLink).where(
            and_(VulnLink.advisory_id == 2, VulnLink.server_id == 101)
        )
    ).scalar_one_or_none()
    assert propagated_link_adv2_s101 is not None

    total_links = db.execute(select(VulnLink)).scalars().all()
    assert len(total_links) == 5

    print("PASS")