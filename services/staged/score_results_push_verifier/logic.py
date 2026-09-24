from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore

SEVEN_AXES = [
    "overall_risk",
    "auth_strength",
    "input_validation",
    "output_sanitization",
    "dependency_security",
    "config_hardening",
    "logging_audit",
]
ALL_AXES_SET = set(SEVEN_AXES)


class ServerScoreDetail(BaseModel):
    server_id: str
    has_overall_risk: bool
    has_all_axes: bool
    latest_score: Optional[int] = None


class VerifyPushResponse(BaseModel):
    servers_checked: int
    passed: int
    failed: int
    details: list[ServerScoreDetail]


async def verify_push(db: Session) -> VerifyPushResponse:
    query = text("""
        WITH latest_per_server_axis AS (
            SELECT
                server_id,
                axis_name,
                label_index,
                scored_at,
                ROW_NUMBER() OVER (
                    PARTITION BY server_id, axis_name
                    ORDER BY scored_at DESC
                ) AS rn
            FROM mcp_llm_axis_scores
        )
        SELECT server_id, axis_name, label_index
        FROM latest_per_server_axis
        WHERE rn = 1
    """)
    result = db.execute(query)
    rows = result.fetchall()

    if not rows:
        return VerifyPushResponse(servers_checked=0, passed=0, failed=0, details=[])

    server_ids = set(r[0] for r in rows)
    server_axes: dict[str, set[str]] = {}
    server_latest_score: dict[str, int] = {}

    for server_id, axis_name, label_index in rows:
        if server_id not in server_axes:
            server_axes[server_id] = set()
        server_axes[server_id].add(axis_name)
        if server_id not in server_latest_score:
            server_latest_score[server_id] = label_index

    details: list[ServerScoreDetail] = []
    passed_count = 0
    failed_count = 0

    for server_id in server_ids:
        axes = server_axes.get(server_id, set())
        has_overall_risk = "overall_risk" in axes
        has_all_axes = ALL_AXES_SET.issubset(axes)
        is_passed = has_overall_risk and has_all_axes

        if is_passed:
            passed_count += 1
        else:
            failed_count += 1

        details.append(
            ServerScoreDetail(
                server_id=server_id,
                has_overall_risk=has_overall_risk,
                has_all_axes=has_all_axes,
                latest_score=server_latest_score.get(server_id),
            )
        )

    return VerifyPushResponse(
        servers_checked=len(server_ids),
        passed=passed_count,
        failed=failed_count,
        details=details,
    )


if __name__ == "__main__":
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    async def self_test():
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE mcp_llm_axis_scores ("
                    "id INTEGER PRIMARY KEY, "
                    "server_id TEXT NOT NULL, "
                    "axis_name TEXT NOT NULL, "
                    "label_index INTEGER, "
                    "scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.commit()

        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()

        base_time = "2024-01-01 00:00:00"
        rows_to_insert = []

        for i in range(1, 3):
            for axis in SEVEN_AXES:
                rows_to_insert.append(
                    {
                        "server_id": f"server_full_{i}",
                        "axis_name": axis,
                        "label_index": i + 1,
                        "scored_at": base_time,
                    }
                )

        for axis in SEVEN_AXES:
            if axis != "overall_risk":
                rows_to_insert.append(
                    {
                        "server_id": "server_missing_overall_risk",
                        "axis_name": axis,
                        "label_index": 5,
                        "scored_at": base_time,
                    }
                )

        for axis in SEVEN_AXES:
            if axis != "auth_strength":
                rows_to_insert.append(
                    {
                        "server_id": "server_missing_auth_strength",
                        "axis_name": axis,
                        "label_index": 6,
                        "scored_at": base_time,
                    }
                )

        for row in rows_to_insert:
            db.execute(
                text(
                    "INSERT INTO mcp_llm_axis_scores "
                    "(server_id, axis_name, label_index, scored_at) "
                    "VALUES (:server_id, :axis_name, :label_index, :scored_at)"
                ),
                row,
            )
        db.commit()

        result = await verify_push(db)

        db.close()
        engine.dispose()

        assert result.servers_checked == 4, f"Expected 4, got {result.servers_checked}"
        assert result.passed == 2, f"Expected 2, got {result.passed}"
        assert result.failed == 2, f"Expected 2, got {result.failed}"

        print("PASS")

    asyncio.run(self_test())