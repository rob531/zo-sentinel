from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


def compute_match_basis_mix(db_session: Session) -> Dict[str, Any]:
    """Compute the distribution of CVE-to-MCP-server link match bases."""
    statement = (
        select(
            VulnLink.match_basis.label("match_basis"),
            func.count(VulnLink.id).label("count"),
            func.avg(VulnLink.match_confidence).label("avg_match_confidence"),
            func.count(func.distinct(McpServerRegistry.server_id)).label(
                "distinct_servers"
            ),
        )
        .select_from(VulnLink)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .group_by(VulnLink.match_basis)
        .order_by(
            func.count(VulnLink.id).desc(),
            VulnLink.match_basis.asc(),
        )
    )

    rows = db_session.execute(statement).mappings().all()

    by_match_basis: List[Dict[str, Any]] = []
    for row in rows:
        confidence = row["avg_match_confidence"]
        by_match_basis.append(
            {
                "match_basis": (
                    str(row["match_basis"])
                    if row["match_basis"] is not None
                    else ""
                ),
                "count": int(row["count"]),
                "avg_match_confidence": (
                    float(confidence) if confidence is not None else 0.0
                ),
                "distinct_servers": int(row["distinct_servers"]),
            }
        )

    return {
        "total_links": sum(item["count"] for item in by_match_basis),
        "by_match_basis": by_match_basis,
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    McpServerRegistry.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    risk_tier="high",
                ),
            ]
        )
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id="CVE-2024-0001",
                    server_id="server-1",
                    match_basis="name_exact",
                    match_value="Server One",
                    match_confidence=0.9,
                ),
                VulnLink(
                    id=2,
                    advisory_id="CVE-2024-0002",
                    server_id="server-2",
                    match_basis="package_name",
                    match_value="@scope/server-two",
                    match_confidence=0.7,
                ),
                VulnLink(
                    id=3,
                    advisory_id="CVE-2024-0003",
                    server_id="server-3",
                    match_basis="repo_url",
                    match_value="https://example.com/server-three",
                    match_confidence=0.5,
                ),
                VulnLink(
                    id=4,
                    advisory_id="CVE-2024-0004",
                    server_id="server-2",
                    match_basis="name_exact",
                    match_value="Server Two",
                    match_confidence=0.9,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln/links/match-basis")
    def match_basis_endpoint(session: Session = Depends(get_session)):
        return compute_match_basis_mix(session)

    with TestSession() as session:
        result = compute_match_basis_mix(session)
        assert result["total_links"] == 4
        assert len(result["by_match_basis"]) == 3
        name_exact = next(
            item
            for item in result["by_match_basis"]
            if item["match_basis"] == "name_exact"
        )
        assert name_exact["avg_match_confidence"] == 0.9

    print("PASS")