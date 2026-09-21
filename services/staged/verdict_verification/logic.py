# services/staged/verdict_verification/logic.py
from typing import Dict, Any

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def verify_verdict_logic(
    server_id: str, expected_tier: str, db: Session = get_session()
) -> Dict[str, Any]:
    """
    Verify a server's risk tier against an expected tier.

    Parameters
    ----------
    server_id: str
        Identifier of the server to verify.
    expected_tier: str
        The tier we expect the server to have.
    db: Session
        SQLAlchemy session (defaults to the app's session).

    Returns
    -------
    dict
        {
            "match": bool,
            "current_tier": str | None,
            "expected_tier": str,
            "discrepancy_reason": str | None,
        }
    """
    record = db.query(McpServerRegistry).filter_by(server_id=server_id).first()
    if record is None:
        return {
            "match": False,
            "current_tier": None,
            "expected_tier": expected_tier,
            "discrepancy_reason": f"Server '{server_id}' not found in registry",
        }

    # The column storing the tier may be named differently across versions.
    # Try the most common names.
    current_tier = getattr(record, "risk_tier", None)
    if current_tier is None:
        current_tier = getattr(record, "tier", None)

    if current_tier == expected_tier:
        return {
            "match": True,
            "current_tier": current_tier,
            "expected_tier": expected_tier,
            "discrepancy_reason": None,
        }

    return {
        "match": False,
        "current_tier": current_tier,
        "expected_tier": expected_tier,
        "discrepancy_reason": (
            f"Expected tier '{expected_tier}' but found '{current_tier}'"
        ),
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Use the same Base that the real app models share.
    from app.db import Base

    # In‑memory SQLite for isolated testing.
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_db = TestSession()

    # Helper to inject the test session into the logic function.
    def get_test_session() -> Session:  # pragma: no cover
        return test_db

    # Seed two server records: one matching, one mismatching.
    # Column names are inferred from the model; adjust if the model differs.
    matching_server = McpServerRegistry(
        server_id="srv_match",
        risk_tier="high",  # expected tier
    )
    mismatching_server = McpServerRegistry(
        server_id="srv_mismatch",
        risk_tier="low",  # different from expected
    )
    test_db.add_all([matching_server, mismatching_server])
    test_db.commit()

    # Verify the matching case.
    result_match = verify_verdict_logic(
        "srv_match", "high", db=test_db
    )
    assert result_match["match"] is True
    assert result_match["current_tier"] == "high"
    assert result_match["expected_tier"] == "high"
    assert result_match["discrepancy_reason"] is None

    # Verify the mismatching case.
    result_mismatch = verify_verdict_logic(
        "srv_mismatch", "high", db=test_db
    )
    assert result_mismatch["match"] is False
    assert result_mismatch["current_tier"] == "low"
    assert result_mismatch["expected_tier"] == "high"
    assert isinstance(result_mismatch["discrepancy_reason"], str)

    print("PASS")