import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8772")
QUERY_URL = os.environ.get("QUERY_SERVICE_URL", "http://localhost:8772")
EXECUTE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://localhost:8772")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("risk_tier_threshold_calibration_api_router")

router = APIRouter(prefix="/api/v1/risk-tier-thresholds", tags=["risk-tier-thresholds"])


class ThresholdEntry(BaseModel):
    tier_name: str = Field(..., description="Risk tier name (e.g., CRITICAL, HIGH, MEDIUM, LOW)")
    min_score: float = Field(..., ge=0.0, le=100.0, description="Minimum trust score for this tier")
    max_score: float = Field(..., ge=0.0, le=100.0, description="Maximum trust score for this tier")
    description: Optional[str] = Field(None, description="Description of the tier")
    is_active: bool = Field(True, description="Whether this threshold is active")


class ThresholdCalibrationRequest(BaseModel):
    method: str = Field("auto", description="Calibration method: auto, manual, or ml")
    target_distribution: Optional[Dict[str, float]] = Field(
        None, description="Target distribution percentages by tier"
    )
    min_samples: int = Field(100, ge=10, description="Minimum samples required for calibration")
    override: bool = Field(False, description="Override existing calibration lock")


class ThresholdCalibrationResponse(BaseModel):
    calibration_id: str
    status: str
    previous_thresholds: List[ThresholdEntry]
    new_thresholds: List[ThresholdEntry]
    distribution_before: Dict[str, int]
    distribution_after: Dict[str, int]
    impact_summary: Dict[str, Any]
    calibrated_at: str


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error(f"ws_query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database query service unavailable: {str(e)}",
        )


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"ws_write failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database write service unavailable: {str(e)}",
        )


def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        response = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        log.error(f"ws_execute failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database execute service unavailable: {str(e)}",
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_threshold_table() -> None:
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS risk_tier_threshold_calibrations (
        calibration_id VARCHAR PRIMARY KEY,
        tier_name VARCHAR NOT NULL,
        min_score DOUBLE NOT NULL,
        max_score DOUBLE NOT NULL,
        description VARCHAR,
        is_active BOOLEAN DEFAULT TRUE,
        method VARCHAR DEFAULT 'auto',
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        calibrated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR,
        notes VARCHAR
    )
    """
    try:
        ws_execute(create_table_sql)
    except Exception as e:
        log.warning(f"Table creation warning (may already exist): {e}")


def ensure_calibration_history_table() -> None:
    create_history_sql = """
    CREATE TABLE IF NOT EXISTS risk_tier_calibration_history (
        history_id VARCHAR PRIMARY KEY,
        calibration_id VARCHAR NOT NULL,
        previous_min_score DOUBLE,
        previous_max_score DOUBLE,
        new_min_score DOUBLE NOT NULL,
        new_max_score DOUBLE NOT NULL,
        change_reason VARCHAR,
        affected_servers_before INTEGER DEFAULT 0,
        affected_servers_after INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    try:
        ws_execute(create_history_sql)
    except Exception as e:
        log.warning(f"History table creation warning (may already exist): {e}")


def get_current_thresholds() -> List[ThresholdEntry]:
    ensure_threshold_table()
    sql = """
    SELECT tier_name, min_score, max_score, description, is_active
    FROM risk_tier_threshold_calibrations
    WHERE is_active = TRUE
    ORDER BY min_score DESC
    """
    rows = ws_query(sql)
    if not rows:
        return get_default_thresholds()
    return [
        ThresholdEntry(
            tier_name=r.get("tier_name", ""),
            min_score=r.get("min_score", 0.0),
            max_score=r.get("max_score", 100.0),
            description=r.get("description"),
            is_active=r.get("is_active", True),
        )
        for r in rows
    ]


def get_default_thresholds() -> List[ThresholdEntry]:
    return [
        ThresholdEntry(
            tier_name="CRITICAL",
            min_score=0.0,
            max_score=20.0,
            description="Critical risk - immediate action required",
            is_active=True,
        ),
        ThresholdEntry(
            tier_name="HIGH",
            min_score=20.0,
            max_score=40.0,
            description="High risk - requires review",
            is_active=True,
        ),
        ThresholdEntry(
            tier_name="MEDIUM",
            min_score=40.0,
            max_score=60.0,
            description="Medium risk - standard monitoring",
            is_active=True,
        ),
        ThresholdEntry(
            tier_name="LOW",
            min_score=60.0,
            max_score=80.0,
            description="Low risk - routine checks",
            is_active=True,
        ),
        ThresholdEntry(
            tier_name="MINIMAL",
            min_score=80.0,
            max_score=100.0,
            description="Minimal risk - trusted servers",
            is_active=True,
        ),
    ]


def get_current_distribution() -> Dict[str, int]:
    thresholds = get_current_thresholds()
    distribution = {}
    for t in thresholds:
        count_sql = f"""
        SELECT COUNT(*) as cnt FROM mcp_server_registry
        WHERE trust_score >= {t.min_score} AND trust_score < {t.max_score}
        """
        rows = ws_query(count_sql)
        distribution[t.tier_name] = rows[0].get("cnt", 0) if rows else 0
    return distribution


def compute_calibration_id() -> str:
    import hashlib
    timestamp = utc_now_iso()
    content = f"{timestamp}:risk_tier_calibration"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def save_calibration_result(
    calibration_id: str,
    thresholds: List[ThresholdEntry],
    method: str,
) -> None:
    ensure_threshold_table()
    for t in thresholds:
        upsert_sql = f"""
        INSERT INTO risk_tier_threshold_calibrations
        (calibration_id, tier_name, min_score, max_score, description, is_active, method, calibrated_at)
        VALUES ('{calibration_id}', '{t.tier_name}', {t.min_score}, {t.max_score}, 
                '{t.description or ''}', {t.is_active}, '{method}', '{utc_now_iso()}')
        ON CONFLICT (calibration_id) DO UPDATE SET
            min_score = EXCLUDED.min_score,
            max_score = EXCLUDED.max_score,
            method = EXCLUDED.method,
            calibrated_at = EXCLUDED.calibrated_at
        """
        try:
            ws_execute(upsert_sql)
        except Exception as e:
            log.error(f"Failed to save threshold {t.tier_name}: {e}")


@router.get("", response_model=List[ThresholdEntry])
def list_thresholds(
    include_inactive: bool = Query(False, description="Include inactive thresholds"),
) -> List[ThresholdEntry]:
    ensure_threshold_table()
    if include_inactive:
        sql = """
        SELECT tier_name, min_score, max_score, description, is_active
        FROM risk_tier_threshold_calibrations
        ORDER BY min_score DESC
        """
    else:
        sql = """
        SELECT tier_name, min_score, max_score, description, is_active
        FROM risk_tier_threshold_calibrations
        WHERE is_active = TRUE
        ORDER BY min_score DESC
        """
    rows = ws_query(sql)
    if not rows:
        return get_default_thresholds()
    return [
        ThresholdEntry(
            tier_name=r.get("tier_name", ""),
            min_score=r.get("min_score", 0.0),
            max_score=r.get("max_score", 100.0),
            description=r.get("description"),
            is_active=r.get("is_active", True),
        )
        for r in rows
    ]


@router.put("", response_model=List[ThresholdEntry])
def update_thresholds(
    thresholds: List[ThresholdEntry],
    calibration_id: Optional[str] = Query(None, description="Optional calibration session ID"),
) -> List[ThresholdEntry]:
    if not thresholds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one threshold must be provided",
        )
    for t in thresholds:
        if t.min_score > t.max_score:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"min_score ({t.min_score}) cannot exceed max_score ({t.max_score}) for tier {t.tier_name}",
            )
    ensure_threshold_table()
    calib_id = calibration_id or compute_calibration_id()
    save_calibration_result(calib_id, thresholds, "manual")
    return thresholds


@router.post("/calibrate", response_model=ThresholdCalibrationResponse)
def calibrate_thresholds(
    request: ThresholdCalibrationRequest,
) -> ThresholdCalibrationResponse:
    ensure_threshold_table()
    ensure_calibration_history_table()
    current_thresholds = get_current_thresholds()
    dist_before = get_current_distribution()
    total_servers = sum(dist_before.values())
    if total_servers < request.min_samples:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient data for calibration. Found {total_servers} servers, need {request.min_samples}",
        )
    calibration_id = compute_calibration_id()
    if request.method == "auto":
        new_thresholds = auto_calibrate_from_distribution(
            current_thresholds, request.target_distribution or {}
        )
    elif request.method == "ml":
        new_thresholds = ml_calibrate_thresholds(current_thresholds, dist_before)
    else:
        new_thresholds = current_thresholds
    dist_after = calculate_projected_distribution(new_thresholds)
    impact_summary = calculate_impact_summary(dist_before, dist_after)
    save_calibration_result(calibration_id, new_thresholds, request.method)
    save_calibration_history(calibration_id, current_thresholds, new_thresholds)
    return ThresholdCalibrationResponse(
        calibration_id=calibration_id,
        status="completed",
        previous_thresholds=current_thresholds,
        new_thresholds=new_thresholds,
        distribution_before=dist_before,
        distribution_after=dist_after,
        impact_summary=impact_summary,
        calibrated_at=utc_now_iso(),
    )


def auto_calibrate_from_distribution(
    current: List[ThresholdEntry], target_dist: Dict[str, float]
) -> List[ThresholdEntry]:
    if not target_dist:
        target_dist = {"CRITICAL": 5.0, "HIGH": 15.0, "MEDIUM": 30.0, "LOW": 30.0, "MINIMAL": 20.0}
    sorted_tiers = sorted(target_dist.items(), key=lambda x: x[0])
    new_thresholds = []
    running_min = 0.0
    for tier_name, target_pct in sorted_tiers:
        tier_range = target_pct
        new_min = running_min
        new_max = running_min + tier_range
        tier_desc = next((t.description for t in current if t.tier_name == tier_name), None)
        new_thresholds.append(
            ThresholdEntry(
                tier_name=tier_name,
                min_score=new_min,
                max_score=new_max,
                description=tier_desc,
                is_active=True,
            )
        )
        running_min = new_max
    return new_thresholds


def ml_calibrate_thresholds(
    current: List[ThresholdEntry], distribution: Dict[str, int]
) -> List[ThresholdEntry]:
    total = sum(distribution.values())
    if total == 0:
        return current
    percentile_based = []
    for t in sorted(current, key=lambda x: x.min_score):
        tier_count = distribution.get(t.tier_name, 0)
        tier_pct = (tier_count / total) * 100.0
        new_thresholds = auto_calibrate_from_distribution(current, {t.tier_name: tier_pct})
        percentile_based = new_thresholds
    return percentile_based if percentile_based else current


def calculate_projected_distribution(thresholds: List[ThresholdEntry]) -> Dict[str, int]:
    distribution = {}
    for t in thresholds:
        count_sql = f"""
        SELECT COUNT(*) as cnt FROM mcp_server_registry
        WHERE trust_score >= {t.min_score} AND trust_score < {t.max_score}
        """
        rows = ws_query(count_sql)
        distribution[t.tier_name] = rows[0].get("cnt", 0) if rows else 0
    return distribution


def calculate_impact_summary(
    before: Dict[str, int], after: Dict[str, int]
) -> Dict[str, Any]:
    total_before = sum(before.values())
    total_after = sum(after.values())
    tier_changes = {}
    for tier in set(list(before.keys()) + list(after.keys())):
        b_val = before.get(tier, 0)
        a_val = after.get(tier, 0)
        change = a_val - b_val
        tier_changes[tier] = {
            "before": b_val,
            "after": a_val,
            "change": change,
            "change_pct": round((change / b_val * 100.0) if b_val > 0 else 0.0, 2),
        }
    return {
        "total_servers_before": total_before,
        "total_servers_after": total_after,
        "tier_changes": tier_changes,
        "net_movement": total_after - total_before,
    }


def save_calibration_history(
    calibration_id: str, previous: List[ThresholdEntry], new: List[ThresholdEntry]
) -> None:
    ensure_calibration_history_table()
    prev_map = {t.tier_name: t for t in previous}
    for t in new:
        import hashlib
        prev = prev_map.get(t.tier_name)
        history_id = hashlib.sha256(
            f"{calibration_id}:{t.tier_name}".encode()
        ).hexdigest()[:16]
        history_sql = f"""
        INSERT INTO risk_tier_calibration_history
        (history_id, calibration_id, previous_min_score, previous_max_score,
         new_min_score, new_max_score, change_reason, affected_servers_before, affected_servers_after)
        VALUES (
            '{history_id}', '{calibration_id}',
            {prev.min_score if prev else 'NULL'}, {prev.max_score if prev else 'NULL'},
            {t.min_score}, {t.max_score},
            'threshold_calibration',
            0, 0
        )
        ON CONFLICT (history_id) DO UPDATE SET
            new_min_score = EXCLUDED.new_min_score,
            new_max_score = EXCLUDED.new_max_score,
            affected_servers_after = EXCLUDED.affected_servers_after
        """
        try:
            ws_execute(history_sql)
        except Exception as e:
            log.error(f"Failed to save calibration history: {e}")


@router.get("/calibration-history", response_model=List[Dict[str, Any]])
def get_calibration_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> List[Dict[str, Any]]:
    ensure_calibration_history_table()
    sql = f"""
    SELECT history_id, calibration_id, tier_name,
           previous_min_score, previous_max_score,
           new_min_score, new_max_score,
           change_reason, affected_servers_before, affected_servers_after,
           created_at
    FROM risk_tier_calibration_history
    ORDER BY created_at DESC
    LIMIT {limit} OFFSET {offset}
    """
    return ws_query(sql)


@router.get("/distribution", response_model=Dict[str, Any])
def get_current_tier_distribution() -> Dict[str, Any]:
    distribution = get_current_distribution()
    total = sum(distribution.values())
    percentages = {
        tier: round((count / total * 100.0) if total > 0 else 0.0, 2)
        for tier, count in distribution.items()
    }
    return {
        "distribution": distribution,
        "percentages": percentages,
        "total_servers": total,
        "computed_at": utc_now_iso(),
    }


@router.post("/revert/{calibration_id}", response_model=List[ThresholdEntry])
def revert_calibration(calibration_id: str) -> List[ThresholdEntry]:
    ensure_threshold_table()
    ensure_calibration_history_table()
    history_sql = f"""
    SELECT DISTINCT ON (tier_name) tier_name, previous_min_score, previous_max_score
    FROM risk_tier_calibration_history
    WHERE calibration_id = '{calibration_id}'
    ORDER BY tier_name, created_at DESC
    """
    history_rows = ws_query(history_sql)
    if not history_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No calibration history found for {calibration_id}",
        )
    reverted_thresholds = []
    for row in history_rows:
        if row.get("previous_min_score") is not None:
            reverted_thresholds.append(
                ThresholdEntry(
                    tier_name=row.get("tier_name", ""),
                    min_score=row.get("previous_min_score", 0.0),
                    max_score=row.get("previous_max_score", 100.0),
                    is_active=True,
                )
            )
    if reverted_thresholds:
        new_calib_id = compute_calibration_id()
        save_calibration_result(new_calib_id, reverted_thresholds, "revert")
    return reverted_thresholds


@router.get("/validate", response_model=Dict[str, Any])
def validate_thresholds(
    thresholds: List[ThresholdEntry] = Query(..., description="Thresholds to validate"),
) -> Dict[str, Any]:
    issues = []
    sorted_thresh = sorted(thresholds, key=lambda x: x.min_score)
    for i, t in enumerate(sorted_thresh):
        if t.min_score > t.max_score:
            issues.append(f"Tier '{t.tier_name}': min_score > max_score")
        if i > 0:
            prev = sorted_thresh[i - 1]
            if t.min_score != prev.max_score:
                issues.append(
                    f"Tier gap/overlap between '{prev.tier_name}' ({prev.max_score}) and '{t.tier_name}' ({t.min_score})"
                )
    total_range = sum(t.max_score - t.min_score for t in thresholds)
    if abs(total_range - 100.0) > 0.01:
        issues.append(f"Total threshold range ({total_range}) does not cover 0-100")
    overlapping = []
    for i, a in enumerate(thresholds):
        for j, b in enumerate(thresholds):
            if i != j and a.tier_name == b.tier_name:
                overlapping.append(a.tier_name)
    if overlapping:
        issues.append(f"Duplicate tier names found: {set(overlapping)}")
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "validated_at": utc_now_iso(),
    }


@router.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "risk_tier_threshold_calibration_api_router",
        "timestamp": utc_now_iso(),
    }


if __name__ == "__main__":
    ensure_threshold_table()
    ensure_calibration_history_table()
    log.info("Risk tier threshold calibration API router initialized")
    import uvicorn

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    def root_health():
        return {"status": "ok", "service": "risk_tier_calibration_api"}

    uvicorn.run(app, host="0.0.0.0", port=8796)