from fastapi import Depends
from sqlalchemy.orm import Session
from typing import List, Optional

from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory


class AliasInfo(BaseModel):
    alias: str
    advisory_id: int
    feed: Optional[str] = None
    severity: Optional[str] = None
    package: Optional[str] = None


class AliasResponse(BaseModel):
    total_advisories: int
    alias_count: int
    aliases: List[AliasInfo]


def get_alias_identity_mapping(
    session: Session = Depends(get_session),
) -> AliasResponse:
    rows = session.query(VulnAdvisory).all()
    total_advisories = len(rows)

    alias_map = {}
    for row in rows:
        # row.aliases is expected to be a list (JSON column)
        for alias in getattr(row, "aliases") or []:
            if alias not in alias_map:
                alias_map[alias] = {
                    "alias": alias,
                    "advisory_id": row.id,
                    "feed": row.feed,
                    "severity": row.severity,
                    "package": row.package,
                }

    alias_list = [AliasInfo(**data) for data in alias_map.values()]
    return AliasResponse(
        total_advisories=total_advisories,
        alias_count=len(alias_map),
        aliases=alias_list,
    )


if __name__ == "__main__":
    # Self‑test using an in‑memory SQLite database
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    # create temporary engine and session
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()

    # seed three advisories with distinct aliases/identities
    adv1 = VulnAdvisory(
        id=1,
        feed="feed1",
        summary="summary1",
        severity="high",
        ecosystem="npm",
        package="pkg1",
        affected_ranges=[],
        aliases=["CVE-2021-0001", "ALIAS-1"],
        source_url="http://example.com/1",
        published_at=datetime.utcnow(),
        fetched_at=datetime.utcnow(),
        identities=["id1"],
        content_hash="hash1",
    )
    adv2 = VulnAdvisory(
        id=2,
        feed="feed2",
        summary="summary2",
        severity="medium",
        ecosystem="pypi",
        package="pkg2",
        affected_ranges=[],
        aliases=["CVE-2021-0002"],
        source_url="http://example.com/2",
        published_at=datetime.utcnow(),
        fetched_at=datetime.utcnow(),
        identities=["id2"],
        content_hash="hash2",
    )
    adv3 = VulnAdvisory(
        id=3,
        feed="feed3",
        summary="summary3",
        severity="low",
        ecosystem="golang",
        package="pkg3",
        affected_ranges=[],
        aliases=["ALIAS-1", "CVE-2021-0003"],
        source_url="http://example.com/3",
        published_at=datetime.utcnow(),
        fetched_at=datetime.utcnow(),
        identities=["id3"],
        content_hash="hash3",
    )

    session.add_all([adv1, adv2, adv3])
    session.commit()

    # invoke the core logic
    result = get_alias_identity_mapping(session)

    # assertions per contract
    assert result.total_advisories == 3
    # unique aliases: CVE-2021-0001, ALIAS-1, CVE-2021-0002, CVE-2021-0003
    assert result.alias_count == 4
    alias_lookup = {a.alias: a.advisory_id for a in result.aliases}
    assert alias_lookup["CVE-2021-0001"] == 1

    print("PASS")