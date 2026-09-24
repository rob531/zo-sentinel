# deps: fastapi, pydantic, sqlalchemy
"""Lane-D scoring precision audit worksheet (PRODUCT_SPEC.md).

WHAT WAS WRONG WITH THIS FILE, AND WHY IT MATTERED
    It queried `mcp_llm_axis_scores` as if the table were WIDE -- one column per
    risk axis:

        SELECT s.overall_risk, s.auth_strength, s.capability_breadth, ...
        FROM mcp_llm_axis_scores s

    The table is LONG. `app/models.py::McpLlmAxisScore` and the live bus agree:
    one ROW per (server_id, axis_name, model_version), carrying `label`,
    `label_index`, `probs` and `p_top`. `overall_risk` is a VALUE of
    `axis_name`, not a column -- as `app/api/verdict_export_api.py`,
    `app/scoring_consumer.py` and `app/api/server_axis_values_api.py` have all
    read it since they were written.

    So all seven names were referents to columns that exist on no plane:
    not on the bus (46 tables / 373 columns), not a mapped attribute, in no
    migration. That is 7 of the 140 missing column referents `referent-verify`
    reports, and every one of them came from line 35 of this file.

    The failure mode is silent, which is why it survived since 2026-07-16.
    The whole SELECT raises, the caller sees an exception or an empty result,
    and a report that produces nothing is indistinguishable from a report that
    found nothing to say.

    It never got that far in practice. Line 5 imported `MCPServerRegistry` and
    `MCPLLMAxisScores`; the classes are `McpServerRegistry` and
    `McpLlmAxisScore`. `orphan_review.csv` has carried the verdict
    BROKEN_IMPORT against this file, with that exact rename, since 2026-07-16.
    Its `__main__` self-test "passed" the whole time because it built its own
    schema in sqlite from the names it wished existed and then asserted against
    it -- a probe that inlines its subject can only confirm itself.

WHAT THIS DOES NOW
    Reads the real long schema through the ORM. No SQL string is assembled
    here at all, so there is no name for a future refactor to leave dangling
    and no interpolation of `model_version` into a query (the old line 49 did
    exactly that).

    Per PRODUCT_SPEC.md the worksheet is: a deterministic seeded stratified
    sample -- N per risk_tier, latest model_version only -- emitting
    {server_id, name, url, risk_tier, verdict, axis labels} with the
    human-verdict column BLANK, plus a summarize mode that computes precision
    from a FILLED worksheet and states PASS/FAIL against the 0.90 bar.

    NO SELF-GRADING. `summarize` counts only rows a human actually filled in.
    A worksheet with no human verdicts returns UNGRADED, never PASS: "nobody
    graded this" and "this scored 100%" are different facts and this module is
    not allowed to confuse them.

    Stratification uses `mcp_server_registry.risk_tier`, which is a real
    column. The previous code derived a tier by thresholding `overall_risk`
    as a float; no such column exists, and inventing a definition for one here
    would be redefining the metric rather than reading it.

GET  /audit_report              JSON worksheet, human_verdict blank
GET  /audit_report/markdown     the same worksheet as a fillable checklist
POST /audit_report/summarize    precision of a filled worksheet vs the bar
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

# The seven axes, in the order the rest of the app already uses them
# (app/api/verdict_export_api.py:28, app/scoring_consumer.py). `overall_risk`
# leads because it is the composite the other six roll up to -- it is an
# axis_name value like the rest, not a separate quantity.
AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

# PRODUCT_SPEC.md lane-D acceptance bar.
PRECISION_BAR = 0.90

# Fixed default so two runs of the report sample the same servers and a human
# can finish a worksheet started yesterday.
DEFAULT_SEED = 20260904


class AxisLabel(BaseModel):
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None


class AuditRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    axis_labels: Dict[str, AxisLabel] = {}
    # Deliberately blank on emission. The whole point of the worksheet is that a
    # human writes this column; a value here that this module produced would be
    # the model grading itself.
    human_verdict: Optional[str] = None


class AuditReport(BaseModel):
    report_date: str
    model_version: str
    seed: int
    per_tier: int
    rows: List[AuditRow]


class AuditSummary(BaseModel):
    verdict: str                       # PASS | FAIL | UNGRADED
    bar: float
    precision: Optional[float] = None
    graded: int = 0
    agreed: int = 0
    total_rows: int = 0
    risk_tier_counts: Dict[str, int] = {}
    note: str = ""


def get_latest_model_version(session: Session) -> Optional[str]:
    """Newest `model_version` present in mcp_llm_axis_scores, or None.

    Returns None rather than the string "unknown": a caller that cannot tell
    "no scores exist" from "the scores are labelled unknown" will report on the
    wrong population.
    """
    return session.execute(
        select(McpLlmAxisScore.model_version)
        .distinct()
        .order_by(McpLlmAxisScore.model_version.desc())
        .limit(1)
    ).scalar()


def get_stratified_sample(
    session: Session,
    model_version: str,
    per_tier: int = 5,
    seed: int = DEFAULT_SEED,
) -> List[AuditRow]:
    """N servers per risk_tier, scored at `model_version`, chosen deterministically.

    Deterministic means: same (seed, per_tier, model_version, population) ->
    same server_ids, in the same order. The shuffle is seeded and the candidate
    list is sorted first, so it does not inherit row order from the database.
    """
    scored = session.execute(
        select(McpLlmAxisScore.server_id)
        .where(McpLlmAxisScore.model_version == model_version)
        .distinct()
    ).scalars().all()
    if not scored:
        return []
    scored_set = set(scored)

    servers = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id.in_(scored_set))
    ).scalars().all()

    by_tier: Dict[str, list] = defaultdict(list)
    for s in servers:
        by_tier[s.risk_tier or "unknown"].append(s)

    rng = random.Random(seed)
    picked = []
    for tier in sorted(by_tier):
        candidates = sorted(by_tier[tier], key=lambda s: s.server_id)
        rng.shuffle(candidates)
        picked.extend(candidates[:per_tier])

    if not picked:
        return []

    axis_rows = session.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.server_id.in_([s.server_id for s in picked]),
        )
    ).scalars().all()

    axes_by_server: Dict[str, Dict[str, AxisLabel]] = defaultdict(dict)
    for r in axis_rows:
        axes_by_server[r.server_id][r.axis_name] = AxisLabel(
            label=r.label, label_index=r.label_index, p_top=r.p_top
        )

    out: List[AuditRow] = []
    for s in picked:
        got = axes_by_server.get(s.server_id, {})
        out.append(
            AuditRow(
                server_id=s.server_id,
                name=s.name,
                url=s.url,
                risk_tier=s.risk_tier,
                verdict=s.verdict,
                # Missing axes stay ABSENT from the dict rather than being
                # filled with a zero. An unscored axis is not a safe axis.
                axis_labels={ax: got[ax] for ax in AXES if ax in got},
                human_verdict=None,
            )
        )
    return out


@router.get("/audit_report", response_model=AuditReport)
def generate_audit_report(
    per_tier: int = Query(5, ge=1, le=200),
    seed: int = Query(DEFAULT_SEED),
    session: Session = Depends(get_session),
) -> AuditReport:
    """Emit the audit worksheet for the latest model_version."""
    model_version = get_latest_model_version(session)
    if not model_version:
        raise HTTPException(status_code=404, detail="No scored model version found")

    rows = get_stratified_sample(session, model_version, per_tier, seed)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No registry servers scored at model_version {model_version}",
        )

    return AuditReport(
        report_date=datetime.now(timezone.utc).isoformat(),
        model_version=model_version,
        seed=seed,
        per_tier=per_tier,
        rows=rows,
    )


@router.get("/audit_report/markdown")
def generate_audit_report_markdown(
    per_tier: int = Query(5, ge=1, le=200),
    seed: int = Query(DEFAULT_SEED),
    session: Session = Depends(get_session),
) -> str:
    """The same worksheet as markdown, with the human-verdict column empty."""
    report = generate_audit_report(per_tier=per_tier, seed=seed, session=session)

    md = ["# Scoring Precision Audit Worksheet", ""]
    md.append(f"**Generated:** {report.report_date}")
    md.append(f"**Model version:** {report.model_version}")
    md.append(f"**Sample:** {report.per_tier} per risk_tier, seed {report.seed}")
    md.append(f"**Bar:** precision >= {PRECISION_BAR:.2f}")
    md.append("")
    md.append("Fill the **Human verdict** column. Leave a row blank to exclude it.")
    md.append("")
    md.append("| Server ID | Name | URL | Risk tier | Model verdict | Human verdict |")
    md.append("|---|---|---|---|---|---|")
    for r in report.rows:
        md.append(
            f"| {r.server_id} | {r.name or ''} | {r.url or ''} | "
            f"{r.risk_tier or ''} | {r.verdict or ''} |  |"
        )

    md.append("")
    md.append("## Axis labels")
    for r in report.rows:
        md.append("")
        md.append(f"### {r.name or r.server_id}")
        for ax in AXES:
            a = r.axis_labels.get(ax)
            if a is None:
                md.append(f"- {ax}: (not scored)")
            else:
                p = "" if a.p_top is None else f" (p_top {a.p_top:.2f})"
                md.append(f"- {ax}: {a.label or '(no label)'}{p}")
    return "\n".join(md)


@router.post("/audit_report/summarize", response_model=AuditSummary)
def summarize_audit_report(report: AuditReport) -> AuditSummary:
    """Precision of a FILLED worksheet against the lane-D bar.

    Only rows carrying a human_verdict are graded. Precision is agreement
    between the model's stored verdict and the human's, over those rows.
    An empty graded set is UNGRADED -- never PASS, and never a divide by zero.
    """
    tiers: Dict[str, int] = defaultdict(int)
    graded = 0
    agreed = 0
    for r in report.rows:
        tiers[r.risk_tier or "unknown"] += 1
        if r.human_verdict is None or str(r.human_verdict).strip() == "":
            continue
        graded += 1
        if (r.verdict or "").strip().lower() == str(r.human_verdict).strip().lower():
            agreed += 1

    if graded == 0:
        return AuditSummary(
            verdict="UNGRADED",
            bar=PRECISION_BAR,
            precision=None,
            graded=0,
            agreed=0,
            total_rows=len(report.rows),
            risk_tier_counts=dict(tiers),
            note="No row carried a human verdict. Nobody graded this; that is "
                 "not a pass.",
        )

    precision = agreed / graded
    return AuditSummary(
        verdict="PASS" if precision >= PRECISION_BAR else "FAIL",
        bar=PRECISION_BAR,
        precision=precision,
        graded=graded,
        agreed=agreed,
        total_rows=len(report.rows),
        risk_tier_counts=dict(tiers),
        note=f"{agreed}/{graded} human-graded rows agreed with the model verdict.",
    )


if __name__ == "__main__":
    # The schema comes from app.models -- the REAL mappers -- not from a set of
    # tables this file invents. That substitution is what let the previous
    # version of this self-test print PASS for seven weeks while the module
    # could not be imported.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app import models  # noqa: F401  -- register the mappers

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    MV = "v2.5"
    for i in range(9):
        tier = ("low", "medium", "high")[i % 3]
        s.add(McpServerRegistry(
            server_id=f"srv{i}", name=f"Server {i}",
            url=f"https://example.invalid/{i}", risk_tier=tier, verdict=tier,
        ))
    n = 0
    for i in range(9):
        for ax in AXES:
            n += 1
            s.add(McpLlmAxisScore(
                id=n, server_id=f"srv{i}", axis_name=ax, label="MODERATE",
                label_index=1, p_top=0.7, model_version=MV,
            ))
    # An older model_version that must NOT leak into a latest-version sample.
    n += 1
    s.add(McpLlmAxisScore(id=n, server_id="srv0", axis_name="overall_risk",
                          label="CRITICAL", label_index=3, p_top=0.99,
                          model_version="v1.0"))
    s.commit()

    assert get_latest_model_version(s) == MV, "latest model_version"

    # --- determinism, and it is a real constraint, not a tautology ---
    a = [r.server_id for r in get_stratified_sample(s, MV, 2, seed=7)]
    b = [r.server_id for r in get_stratified_sample(s, MV, 2, seed=7)]
    assert a == b, f"not deterministic for a fixed seed: {a} != {b}"
    assert len(a) == 6, f"expected 2 per tier x 3 tiers, got {len(a)}: {a}"
    # NEGATIVE CONTROL: if the seed were ignored the assertion above would pass
    # anyway, so prove a different seed can reach a different sample.
    seeds = {tuple(r.server_id for r in get_stratified_sample(s, MV, 1, seed=k))
             for k in range(12)}
    assert len(seeds) > 1, f"seed has no effect -- sampling is not seeded: {seeds}"

    rows = get_stratified_sample(s, MV, 5, seed=7)
    assert rows and all(r.human_verdict is None for r in rows), \
        "worksheet must emit a BLANK human verdict"
    assert set(rows[0].axis_labels) == set(AXES), \
        f"all 7 axes should pivot in: {sorted(rows[0].axis_labels)}"
    assert rows[0].axis_labels["overall_risk"].label == "MODERATE", \
        "the v1.0 row must not leak into a v2.5 sample"

    rep = AuditReport(report_date="t", model_version=MV, seed=7, per_tier=5,
                      rows=rows)

    # --- summarize: UNGRADED, PASS and FAIL are three outcomes, not two ---
    blank = summarize_audit_report(rep)
    assert blank.verdict == "UNGRADED" and blank.precision is None, \
        f"an unfilled worksheet must be UNGRADED, got {blank.verdict}"

    filled = rep.model_copy(deep=True)
    for r in filled.rows:
        r.human_verdict = r.verdict            # 100% agreement
    good = summarize_audit_report(filled)
    assert good.verdict == "PASS" and good.precision == 1.0, good

    # NEGATIVE CONTROL: the bar must be able to fail. Disagree on enough rows
    # that precision lands under 0.90 and require FAIL.
    bad = rep.model_copy(deep=True)
    for i, r in enumerate(bad.rows):
        r.human_verdict = r.verdict if i % 2 == 0 else "definitely-not-this"
    worse = summarize_audit_report(bad)
    assert worse.verdict == "FAIL", f"bar never fails: {worse}"
    assert worse.precision is not None and worse.precision < PRECISION_BAR, worse

    print("PASS")
