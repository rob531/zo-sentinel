from sqlalchemy import text
from sqlalchemy.orm import Session

from .logic import search_registry_logic, SearchResponse


def search_registry(
    q: str | None = None,
    source: str | None = None,
    trust_min: float | None = None,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = None,
) -> SearchResponse:
    return search_registry_logic(session, q, source, trust_min, tier, limit, offset)