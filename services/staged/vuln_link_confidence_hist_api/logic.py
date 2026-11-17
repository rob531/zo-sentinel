from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


router = APIRouter(prefix="/api")


class ConfidenceBucket(BaseModel):
    label: str
    count: int
    pct: float


class MatchBasisSummary(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class ConfidenceHistogramResponse(BaseModel):
    total_links: int
    buckets: List[ConfidenceBucket]
    by_basis: List[MatchBasisSummary]
    generated_at: str


BUCKET_LABELS = (
    "0.0-0.25",
    "0.25-0.5",
    "0.5-0.75",
    "0.75-1.0",
)


def _bucket_for(confidence: float) -> str:
    if confidence < 0.25:
        return BUCKET_LABELS[0]
    if confidence < 0.5:
        return BUCKET_LABELS[1]
    if confidence < 0.75:
        return BUCKET_LABELS[2]
    return BUCKET_LABELS[3]


def _build_histogram(
    session: Session,
    match_basis: Optional[str] = None,
    min_confidence: float = 0.0,
) -> ConfidenceHistogramResponse:
    statement = (
        select(
            VulnLink.match_basis,
            VulnLink.match_confidence,
        )
        .join(
            McpServerRegistry,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
        .where(VulnLink.match_confidence >= min_confidence)
    )

    if match_basis is not None:
        statement = statement.where(VulnLink.match_basis == match_basis)

    rows = session.execute(statement).all()

    bucket_counts: Dict[str, int] = {label: 0 for label in BUCKET_LABELS}
    basis_counts: Dict[str, int] = defaultdict(int)
    basis_confidence_totals: Dict[str, float] = defaultdict(float)

    for basis, confidence in rows:
        confidence_value = float(confidence or 0.0)
        bucket_counts[_bucket_for(confidence_value)] += 1
        basis_key = str(basis or "")
        basis_counts[basis_key] += 1
        basis_confidence_totals[basis_key] += confidence_value

    total_links = len(rows)
    buckets = [
        ConfidenceBucket(
            label=label,
            count=bucket_counts[label],
            pct=(bucket_counts[label] / total_links * 100.0) if total_links else 0.0,
        )
        for label in BUCKET_LABELS
    ]

    by_basis = [
        MatchBasisSummary(
            match_basis=basis,
            count=basis_counts[basis],
            avg_confidence=basis_confidence_totals[basis] / basis_counts[basis],
        )
        for basis in sorted(basis_counts)
    ]

    return ConfidenceHistogramResponse(
        total_links=total_links,
        buckets=buckets,
        by_basis=by_basis,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/vuln/links/confidence-histogram",
    response_model=ConfidenceHistogramResponse,
)
def get_confidence_histogram(
    match_basis: Optional[str] = None,
    min_confidence: float = 0.0,
    session: Session = Depends(get_session),
) -> ConfidenceHistogramResponse:
    return _build_histogram(
        session=session,
        match_basis=match_basis,
        min_confidence=min_confidence,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Server One"),
                McpServerRegistry(server_id="server-2", name="Server Two"),
            ]
        )
        session.flush()
        session.add_all(
            [
                VulnLink(
                    advisory_id="CVE-2024-0001",
                    server_id="server-1",
                    match_basis="advisory",
                    match_confidence=0.10,
                    match_value="CVE-2024-0001",
                ),
                VulnLink(
                    advisory_id="CVE-2024-0002",
                    server_id="server-1",
                    match_basis="advisory",
                    match_confidence=0.30,
                    match_value="CVE-2024-0002",
                ),
                VulnLink(
                    advisory_id="CVE-2024-0003",
                    server_id="server-1",
                    match_basis="advisory",
                    match_confidence=0.60,
                    match_value="CVE-2024-0003",
                ),
                VulnLink(
                    advisory_id="CVE-2024-0004",
                    server_id="server-2",
                    match_basis="package",
                    match_confidence=0.80,
                    match_value="package-4",
                ),
                VulnLink(
                    advisory_id="CVE-2024-0005",
                    server_id="server-2",
                    match_basis="package",
                    match_confidence=0.90,
                    match_value="package-5",
                ),
                VulnLink(
                    advisory_id="CVE-2024-0006",
                    server_id="server-2",
                    match_basis="package",
                    match_confidence=0.40,
                    match_value="package-6",
                ),
            ]
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        result = _build_histogram(session)

    assert result.total_links == 6
    assert [bucket.label for bucket in result.buckets] == list(BUCKET_LABELS)
    assert sum(bucket.count for bucket in result.buckets) == result.total_links
    assert len(result.by_basis) == 2

    print("PASS")