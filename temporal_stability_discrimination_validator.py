import logging
import sys
import requests
from datetime import datetime, timezone

SERVICE_NAME = "temporal_stability_discrimination_validator"
WRITE_SERVICE_URL = "http://localhost:8772"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")]
)
logger = logging.getLogger(__name__)

SIGNAL_NAME = "temporal_stability"
MIN_DISTINCT_SCORES = 20


def ws_query(sql: str) -> list:
    payload = {"sql": sql, "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def check_temporal_stability_discrimination() -> dict:
    sql = """
        SELECT COUNT(DISTINCT score) AS distinct_scores
        FROM mcp_signal_enrichments
        WHERE signal_name = 'temporal_stability'
    """
    rows = ws_query(sql)
    if not rows:
        logger.warning("mcp_signal_enrichments returned no rows for temporal_stability")
        return {"signal": SIGNAL_NAME, "distinct_scores": 0, "discriminatory": False}
    distinct_scores = rows[0].get("distinct_scores", 0)
    discriminatory = distinct_scores > MIN_DISTINCT_SCORES
    logger.info(
        "Temporal stability discrimination check: distinct_scores=%d threshold=%d discriminatory=%s",
        distinct_scores, MIN_DISTINCT_SCORES, discriminatory
    )
    return {
        "signal": SIGNAL_NAME,
        "distinct_scores": distinct_scores,
        "discriminatory": discriminatory,
    }


def main():
    ts = datetime.now(timezone.utc).isoformat()
    logger.info("[%s] Starting temporal_stability discrimination validation", ts)

    result = check_temporal_stability_discrimination()

    if result["discriminatory"]:
        logger.info(
            "PASS: signal '%s' has %d distinct scores (exceeds threshold of %d) — GOOD SIGNAL",
            result["signal"], result["distinct_scores"], MIN_DISTINCT_SCORES
        )
        sys.exit(0)
    else:
        logger.warning(
            "FAIL: signal '%s' has only %d distinct scores (threshold %d) — BAD SIGNAL",
            result["signal"], result["distinct_scores"], MIN_DISTINCT_SCORES
        )
        sys.exit(1)


if __name__ == "__main__":
    main()