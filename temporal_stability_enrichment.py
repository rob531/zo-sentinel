import math
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)
SIGNAL_NAME = "temporal_stability"
VERSION = "1.0.0"
MAX_SCORE = 100.0


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _softmax_weight(value: float, all_values: list[float]) -> float:
    if not all_values:
        return 0.5
    exp_vals = [math.exp(v) for v in all_values]
    total = sum(exp_vals)
    if total == 0:
        return 0.5
    return math.exp(value) / total


def _log_normalize(value: float, scale: float = 100.0) -> float:
    if value <= 0:
        return 0.0
    return min(scale, math.log1p(value) / math.log(scale + 1) * scale)


def _hash_string(s: str) -> float:
    h = hashlib.sha256(s.encode("utf-8")).digest()
    val = int.from_bytes(h[:4], "big")
    return (val / 0xFFFFFFFF) * MAX_SCORE


def parse_iso_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _score_age_days(age_days: float | None) -> tuple[float, str, str]:
    if age_days is None:
        return 0.0, "age_score", "missing"
    if age_days < 1:
        return 2.0, "age_score", "newborn"
    if age_days >= 1825:
        return 100.0, "age_score", "ancient"
    normalized = _sigmoid((age_days - 365) / 182.5)
    return normalized * 100.0, "age_score", f"{age_days:.0f} days"


def _score_recency(last_updated: str | None, now: datetime) -> tuple[float, str, str]:
    if not last_updated:
        return 0.0, "recency_score", "missing"
    updated_dt = parse_iso_date(last_updated)
    if not updated_dt:
        return 0.0, "recency_score", "parse_error"
    updated_utc = updated_dt if updated_dt.tzinfo else updated_dt.replace(tzinfo=timezone.utc)
    age_seconds = (now - updated_utc).total_seconds()
    age_days = max(0, age_seconds / 86400.0)
    if age_days <= 1:
        return 100.0, "recency_score", "today"
    if age_days > 730:
        return 0.0, "recency_score", "stale"
    normalized = _sigmoid((30 - age_days) / 30)
    return normalized * 100.0, "recency_score", f"{age_days:.1f} days ago"


def _score_establishment(first_seen: str | None, last_updated: str | None, now: datetime) -> tuple[float, str, str]:
    if not first_seen:
        return 50.0, "establishment_score", "missing"
    first_dt = parse_iso_date(first_seen)
    if not first_dt:
        return 50.0, "establishment_score", "parse_error"
    first_utc = first_dt if first_dt.tzinfo else first_dt.replace(tzinfo=timezone.utc)
    if last_updated:
        last_dt = parse_iso_date(last_updated)
        if last_dt:
            last_utc = last_dt if last_dt.tzinfo else last_dt.replace(tzinfo=timezone.utc)
            duration_seconds = (last_utc - first_utc).total_seconds()
            if duration_seconds <= 0:
                return 75.0, "establishment_score", "single_version"
            duration_days = duration_seconds / 86400.0
            stability_ratio = min(1.0, duration_days / 730.0)
            return stability_ratio * 100.0, "establishment_score", f"{duration_days:.0f} days active"
    age_days = (now - first_utc).total_seconds() / 86400.0
    return min(100.0, (age_days / 7.3)), "establishment_score", f"{age_days:.0f} days"


def _score_update_rhythm(update_frequency: str | None) -> tuple[float, str, str]:
    if not update_frequency:
        return 50.0, "update_rhythm_score", "missing"
    freq_lower = update_frequency.lower().strip()
    if freq_lower in ("daily", "very active", "nightly"):
        return 95.0, "update_rhythm_score", "daily"
    elif freq_lower in ("weekly", "active"):
        return 85.0, "update_rhythm_score", "weekly"
    elif freq_lower in ("monthly", "moderate"):
        return 75.0, "update_rhythm_score", "monthly"
    elif freq_lower in ("quarterly", "low"):
        return 55.0, "update_rhythm_score", "quarterly"
    elif freq_lower in ("yearly", "rare"):
        return 30.0, "update_rhythm_score", "yearly"
    elif freq_lower in ("never", "abandoned", "static"):
        return 5.0, "update_rhythm_score", "never"
    else:
        return 50.0, "update_rhythm_score", f"unknown: {update_frequency}"


def _score_version_depth(version_history_count: int | None) -> tuple[float, str, str]:
    if version_history_count is None:
        return 50.0, "version_depth_score", "missing"
    if version_history_count <= 0:
        return 0.0, "version_depth_score", "no_versions"
    if version_history_count >= 100:
        return 100.0, "version_depth_score", "extensive"
    normalized = _sigmoid((math.log(version_history_count + 1) - 2) / 2)
    return normalized * 100.0, "version_depth_score", f"{version_history_count} versions"


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    age_days = metadata.get("age_days")
    last_updated = metadata.get("last_updated")
    first_seen = metadata.get("first_seen")
    update_frequency = metadata.get("update_frequency")
    version_history_count = metadata.get("version_history_count")

    age_score, age_key, age_detail = _score_age_days(age_days)
    recency_score, recency_key, recency_detail = _score_recency(last_updated, now)
    establishment_score, est_key, est_detail = _score_establishment(first_seen, last_updated, now)
    rhythm_score, rhythm_key, rhythm_detail = _score_update_rhythm(update_frequency)
    version_score, version_key, version_detail = _score_version_depth(version_history_count)

    dim_scores = {
        age_key: age_score,
        recency_key: recency_score,
        est_key: establishment_score,
        rhythm_key: rhythm_score,
        version_key: version_score,
    }
    dim_weights = {
        age_key: 0.20,
        recency_key: 0.30,
        est_key: 0.15,
        rhythm_key: 0.20,
        version_key: 0.15,
    }

    total_score = sum(dim_scores[k] * dim_weights[k] for k in dim_scores)
    final_score = max(0.0, min(MAX_SCORE, total_score))

    evidence: dict[str, Any] = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "computed_at": now.isoformat(),
        "final_score": round(final_score, 4),
        "dimensions": {
            age_key: {
                "partial": round(age_score, 4),
                "weight": dim_weights[age_key],
                "detail": age_detail,
                "field_used": "age_days",
            },
            recency_key: {
                "partial": round(recency_score, 4),
                "weight": dim_weights[recency_key],
                "detail": recency_detail,
                "field_used": "last_updated",
            },
            est_key: {
                "partial": round(establishment_score, 4),
                "weight": dim_weights[est_key],
                "detail": est_detail,
                "field_used": "first_seen",
            },
            rhythm_key: {
                "partial": round(rhythm_score, 4),
                "weight": dim_weights[rhythm_key],
                "detail": rhythm_detail,
                "field_used": "update_frequency",
            },
            version_key: {
                "partial": round(version_score, 4),
                "weight": dim_weights[version_key],
                "detail": version_detail,
                "field_used": "version_history_count",
            },
        },
        "fields_present": {
            "age_days": age_days is not None,
            "last_updated": last_updated is not None,
            "first_seen": first_seen is not None,
            "update_frequency": update_frequency is not None,
            "version_history_count": version_history_count is not None,
        },
    }
    return final_score, evidence


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    LOG.info("temporal_stability_enrichment standalone smoke run")

    test_cases: list[tuple[str, dict[str, Any]]] = [
        ("full_metadata_healthy", {
            "age_days": 730,
            "last_updated": "2026-01-15T00:00:00Z",
            "first_seen": "2024-01-01T00:00:00Z",
            "update_frequency": "monthly",
            "version_history_count": 24,
        }),
        ("full_metadata_stale", {
            "age_days": 2000,
            "last_updated": "2020-01-01T00:00:00Z",
            "first_seen": "2020-01-01T00:00:00Z",
            "update_frequency": "never",
            "version_history_count": 1,
        }),
        ("partial_only_age", {
            "age_days": 90,
        }),
        ("minimal_newborn", {
            "age_days": 0.5,
            "last_updated": "2026-06-01T00:00:00Z",
            "first_seen": "2026-06-01T00:00:00Z",
            "update_frequency": "daily",
            "version_history_count": 2,
        }),
        ("empty_metadata", {}),
    ]

    for label, meta in test_cases:
        score, evidence = compute_score(meta)
        LOG.info("[%s] score=%.4f fields_present=%s", label, score, evidence.get("fields_present"))


if __name__ == "__main__":
    run()