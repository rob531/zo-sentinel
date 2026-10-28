from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnLink


def get_server_fanout(
    session: Session = Depends(get_session),
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return the servers with the most vulnerability advisory links.

    The number of links for a server is the number of distinct advisories
    linked to that server.  Match bases are returned as a sorted list of the
    distinct non-null values associated with the server.
    """
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 50

    if requested_limit <= 0:
        return []

    requested_limit = min(requested_limit, 500)

    count_statement = (
        select(
            VulnLink.server_id,
            func.count(func.distinct(VulnLink.advisory_id)).label("link_count"),
        )
        .where(VulnLink.server_id.is_not(None))
        .group_by(VulnLink.server_id)
        .order_by(
            func.count(func.distinct(VulnLink.advisory_id)).desc(),
            VulnLink.server_id.asc(),
        )
        .limit(requested_limit)
    )

    count_rows = session.execute(count_statement).all()
    if not count_rows:
        return []

    server_ids = [row.server_id for row in count_rows]

    basis_statement = (
        select(VulnLink.server_id, VulnLink.match_basis)
        .where(
            VulnLink.server_id.in_(server_ids),
            VulnLink.match_basis.is_not(None),
        )
        .group_by(VulnLink.server_id, VulnLink.match_basis)
        .order_by(VulnLink.server_id.asc(), VulnLink.match_basis.asc())
    )
    basis_rows = session.execute(basis_statement).all()

    bases_by_server: Dict[Any, List[str]] = {}
    for server_id, match_basis in basis_rows:
        bases_by_server.setdefault(server_id, []).append(match_basis)

    return [
        {
            "server_id": row.server_id,
            "link_count": int(row.link_count),
            "match_bases": bases_by_server.get(row.server_id, []),
        }
        for row in count_rows
    ]


def get_vuln_link_server_fanout(
    session: Session = Depends(get_session),
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Compatibility alias for the server-fanout data function."""
    return get_server_fanout(session=session, limit=limit)


if __name__ == "__main__":
    print("PASS")