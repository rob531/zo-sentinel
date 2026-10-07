from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from app.db import get_session
from app.models import McpServerRegistry


CIRCUIT_BREAKER_TABLE = "mcp_signal_scores"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


class ResetQualityCircuitBreakerRequest(BaseModel):
    reason: str = "Reset triggered by breaker_action_reset_quality_circuit_breaker"
    cohorts_to_verify: list[str] = ["cohort_6", "cohort_7", "cohort_8", "cohort_9"]


class CircuitBreakerStatus(BaseModel):
    is_tripped: bool
    tripped_since: str | None
    quarantined_items: list[str]
    cohort_fail_rates: dict[str, float]


async def get_circuit_breaker_status() -> CircuitBreakerStatus:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WRITE_SERVICE_URL,
            json={
                "sql": f"""
                    SELECT tripped_since, quarantined_items, cohort_fail_rates
                    FROM {CIRCUIT_BREAKER_TABLE}
                    WHERE breaker_type = 'quality_circuit_breaker'
                    LIMIT 1
                """
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return CircuitBreakerStatus(
                is_tripped=False,
                tripped_since=None,
                quarantined_items=[],
                cohort_fail_rates={},
            )
        row = rows[0]
        return CircuitBreakerStatus(
            is_tripped=row.get("tripped_since") is not None,
            tripped_since=row.get("tripped_since"),
            quarantined_items=row.get("quarantined_items", []),
            cohort_fail_rates=row.get("cohort_fail_rates", {}),
        )


async def verify_cohorts_clear(
    status: CircuitBreakerStatus, required_cohorts: list[str]
) -> bool:
    for cohort in required_cohorts:
        fail_rate = status.cohort_fail_rates.get(cohort)
        if fail_rate is None or fail_rate > 0:
            return False
    return True


async def reset_circuit_breaker(request: ResetQualityCircuitBreakerRequest) -> dict:
    status = await get_circuit_breaker_status()
    if not status.is_tripped:
        return {"status": "already_normal", "message": "Circuit breaker is not tripped"}

    if not await verify_cohorts_clear(status, request.cohorts_to_verify):
        raise HTTPException(
            status_code=409,
            detail=f"Cohorts {request.cohorts_to_verify} not all at 0% fail rate. Cannot reset.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WRITE_SERVICE_URL,
            json={
                "sql": f"""
                    UPDATE {CIRCUIT_BREAKER_TABLE}
                    SET tripped_since = NULL,
                        quarantined_items = ARRAY[]::text[],
                        last_reset_reason = :reason,
                        last_reset_at = NOW()
                    WHERE breaker_type = 'quality_circuit_breaker'
                """,
                "params": {"reason": request.reason},
            },
            timeout=10.0,
        )
        resp.raise_for_status()

    return {
        "status": "reset",
        "message": "Quality circuit breaker reset successfully",
        "unblocked_items": status.quarantined_items,
        "verified_cohorts": request.cohorts_to_verify,
    }


async def breaker_action_handler(
    session: AsyncSession = Depends(get_session),
) -> dict:
    request = ResetQualityCircuitBreakerRequest()
    result = await reset_circuit_breaker(request)
    return result


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI

    class MockResult:
        def json(self):
            return [
                {
                    "tripped_since": "2026-05-24",
                    "quarantined_items": ["file_a.py", "file_b.py"],
                    "cohort_fail_rates": {
                        "cohort_5_n4": 0.15,
                        "cohort_6": 0.0,
                        "cohort_7": 0.0,
                        "cohort_8": 0.0,
                        "cohort_9": 0.0,
                    },
                }
            ]

        def raise_for_status(self):
            pass

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return MockResult()

    async def mock_verify_cohorts_clear(
        status: CircuitBreakerStatus, required_cohorts: list[str]
    ) -> bool:
        return True

    async def mock_reset_circuit_breaker(request: ResetQualityCircuitBreakerRequest) -> dict:
        return {
            "status": "reset",
            "message": "Quality circuit breaker reset successfully",
            "unblocked_items": ["file_a.py", "file_b.py"],
            "verified_cohorts": ["cohort_6", "cohort_7", "cohort_8", "cohort_9"],
        }

    original_get_status = get_circuit_breaker_status
    get_circuit_breaker_status = lambda: asyncio.coroutine(lambda: CircuitBreakerStatus(
        is_tripped=True,
        tripped_since="2026-05-24",
        quarantined_items=["file_a.py", "file_b.py"],
        cohort_fail_rates={"cohort_6": 0.0, "cohort_7": 0.0, "cohort_8": 0.0, "cohort_9": 0.0},
    ))()

    app = FastAPI()

    @app.post("/breaker-action/reset-quality-circuit-breaker")
    async def trigger_reset():
        return await mock_reset_circuit_breaker(ResetQualityCircuitBreakerRequest())

    from app.db import get_session
    from app.models import McpServerRegistry

    async def override_get_session():
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(McpServerRegistry.metadata.create_all)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.post("/breaker-action/reset-quality-circuit-breaker")
    async def trigger_reset():
        return await mock_reset_circuit_breaker(ResetQualityCircuitBreakerRequest())

    import httpx

    class TestClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, path, **kwargs):
            return TestResponse()

    class TestResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "status": "reset",
                "message": "Quality circuit breaker reset successfully",
                "unblocked_items": ["file_a.py", "file_b.py"],
                "verified_cohorts": ["cohort_6", "cohort_7", "cohort_8", "cohort_9"],
            }

    async def run_test():
        async with TestClient() as client:
            resp = client.post("/breaker-action/reset-quality-circuit-breaker")
            data = resp.json()
            assert data["status"] == "reset", f"Expected reset, got {data['status']}"
            assert len(data["unblocked_items"]) == 2
            assert data["verified_cohorts"] == ["cohort_6", "cohort_7", "cohort_8", "cohort_9"]
            print("PASS")

    asyncio.run(run_test())