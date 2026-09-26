# deps: fastapi, pydantic, sqlalchemy
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import csv
import io

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, VulnLink

router = APIRouter(prefix="/api", tags=["server_registry_csv_export"])


def get_csv_response(rows: list) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "server_id", "name", "registry_source", "url", "description",
        "trust_score", "verdict", "verdict_reasoning", "confidence",
        "risk_tier", "p_top", "p_critical", "vuln_link_count",
        "scan_count", "first_seen", "last_seen", "last_scanned"
    ])

    for row in rows:
        writer.writerow([
            row.server_id,
            row.name or "",
            row.registry_source or "",
            row.url or "",
            row.description or "",
            row.trust_score,
            row.verdict or "",
            row.verdict_reasoning or "",
            row.confidence,
            row.risk_tier or "",
            row.p_top,
            row.p_critical,
            row.vuln_link_count,
            row.scan_count,
            row.first_seen.isoformat() if row.first_seen else "",
            row.last_seen.isoformat() if row.last_seen else "",
            row.last_scanned.isoformat() if row.last_scanned else "",
        ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=registry_export_"
                f"{datetime.utcnow().strftime('%Y%m%d')}.csv"
            )
        },
    )


@router.get("/servers/export")
def export_servers(
    risk_tier: str | None = Query(default=None, description="Filter by risk tier"),
    verdict: str | None = Query(default=None, description="Filter by verdict"),
    registry_source: str | None = Query(default=None, description="Filter by registry source"),
    limit: int = Query(default=10000, le=50000),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.description,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.confidence,
            McpServerRegistry.risk_tier,
            McpServerRegistry.scan_count,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            func.count(VulnLink.id).label("vuln_link_count"),
        )
        .outerjoin(
            McpLlmAxisScore,
            (McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            & (McpLlmAxisScore.axis_name == "overall_risk"),
        )
        .outerjoin(VulnLink, McpServerRegistry.server_id == VulnLink.server_id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.url,
            McpServerRegistry.description,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.confidence,
            McpServerRegistry.risk_tier,
            McpServerRegistry.scan_count,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
        )
        .order_by(McpServerRegistry.last_seen.desc())
    )

    if risk_tier:
        rows = rows.filter(McpServerRegistry.risk_tier == risk_tier)
    if verdict:
        rows = rows.filter(McpServerRegistry.verdict == verdict)
    if registry_source:
        rows = rows.filter(McpServerRegistry.registry_source == registry_source)

    rows = rows.limit(limit).all()
    return get_csv_response(rows)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base
    from app.main import app as _main_app

    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        db.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, registry_source, url, trust_score, verdict, "
                "confidence, risk_tier, scan_count, first_seen, last_seen) VALUES "
                "('srv1','Server Alpha','github','https://github.com/alpha',0.9,'safe',0.95,'LOW',1,'2024-01-01','2024-06-15'),"
                "('srv2','Server Beta','npm','https://npmjs.com/beta',0.7,'suspicious',0.85,'MEDIUM',2,'2024-02-01','2024-06-20'),"
                "('srv3','Server Gamma','github','https://github.com/gamma',0.4,'malicious',0.75,'HIGH',3,'2024-03-01','2024-06-25')"
            )
        )
        db.execute(
            text(
                "INSERT INTO mcp_llm_axis_scores "
                "(server_id, axis_name, p_top, p_critical) VALUES "
                "('srv1','overall_risk',0.8,0.1),"
                "('srv2','overall_risk',0.6,0.3),"
                "('srv3','overall_risk',0.4,0.5)"
            )
        )
        db.execute(
            text(
                "INSERT INTO vuln_links (id, advisory_id, server_id, match_confidence) VALUES "
                "(1,'CVE-2024-0001','srv1',0.95),"
                "(2,'CVE-2024-0001','srv2',0.80),"
                "(3,'CVE-2024-0002','srv3',0.90)"
            )
        )
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    _that_app.dependency_overrides[get_session] = _override_session
    _c = TestClient(_that_app)

    resp = _c.get("/api/servers/export")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.headers["content-type"] == "text/csv"
    assert "attachment; filename=registry_export_" in resp.headers["content-disposition"]
    lines = resp.content.decode().splitlines()
    assert len(lines) >= 4, f"Expected >=4 lines (header + 3 rows), got {len(lines)}"

    resp2 = _c.get("/api/servers/export?risk_tier=HIGH")
    assert resp2.status_code == 200
    lines2 = resp2.content.decode().splitlines()
    assert len(lines2) == 2, f"Expected 2 lines for HIGH filter, got {len(lines2)}"

    resp3 = _c.get("/api/servers/export?verdict=safe")
    assert resp3.status_code == 200
    lines3 = resp3.content.decode().splitlines()
    assert len(lines3) == 2, f"Expected 2 lines for safe filter, got {len(lines3)}"

    resp4 = _c.get("/api/servers/export?registry_source=npm")
    assert resp4.status_code == 200
    lines4 = resp4.content.decode().splitlines()
    assert len(lines4) == 2, f"Expected 2 lines for npm filter, got {len(lines4)}"

    resp5 = _c.get("/api/servers/export?limit=2")
    assert resp5.status_code == 200
    lines5 = resp5.content.decode().splitlines()
    assert len(lines5) == 3, f"Expected 3 lines for limit=2, got {len(lines5)}"

    print("PASS")
