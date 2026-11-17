from typing import Any, Dict, Optional

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpLlmAxisScore


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


async def get_axis_critical_top_gap(
    axis: Optional[str] = None,
    session: Optional[AsyncSession] = Depends(get_session),
) -> Dict[str, Any]:
    if hasattr(axis, "execute"):
        axis, session = (session if isinstance(session, str) else None), axis

    gap = McpLlmAxisScore.p_critical - McpLlmAxisScore.p_top

    statement = select(
        McpLlmAxisScore.axis_name,
        func.count(McpLlmAxisScore.id).label("rows"),
        func.sum(
            case(
                (McpLlmAxisScore.p_critical > McpLlmAxisScore.p_top, 1),
                else_=0,
            )
        ).label("critical_gt_top"),
        func.sum(
            case(
                (McpLlmAxisScore.escalated.is_(True), 1),
                else_=0,
            )
        ).label("escalated"),
        func.avg(gap).label("avg_gap"),
        func.max(gap).label("max_gap"),
    ).where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))

    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    statement = statement.group_by(McpLlmAxisScore.axis_name)
    result = await session.execute(statement)

    axes_by_name: Dict[str, Dict[str, Any]] = {}
    for row in result:
        axes_by_name[row.axis_name] = {
            "axis_name": row.axis_name,
            "rows": int(row.rows or 0),
            "critical_gt_top": int(row.critical_gt_top or 0),
            "escalated": int(row.escalated or 0),
            "avg_gap": float(row.avg_gap) if row.avg_gap is not None else 0.0,
            "max_gap": float(row.max_gap) if row.max_gap is not None else 0.0,
        }

    axes = [
        axes_by_name[name]
        for name in AXIS_NAMES
        if name in axes_by_name
    ]

    return {
        "axes": axes,
        "totals": {
            "rows": sum(item["rows"] for item in axes),
            "critical_gt_top": sum(item["critical_gt_top"] for item in axes),
            "escalated": sum(item["escalated"] for item in axes),
        },
    }


async def get_critical_top_gap(
    axis: Optional[str] = None,
    session: Optional[AsyncSession] = Depends(get_session),
) -> Dict[str, Any]:
    return await get_axis_critical_top_gap(axis=axis, session=session)


async def calculate_axis_critical_top_gap(
    axis: Optional[str] = None,
    session: Optional[AsyncSession] = Depends(get_session),
) -> Dict[str, Any]:
    return await get_axis_critical_top_gap(axis=axis, session=session)


async def compute_axis_critical_top_gap(
    axis: Optional[str] = None,
    session: Optional[AsyncSession] = Depends(get_session),
) -> Dict[str, Any]:
    return await get_axis_critical_top_gap(axis=axis, session=session)


if __name__ == "__main__":
    import asyncio

    import httpx
    from fastapi import FastAPI
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/axes/critical-top-gap")
    async def axis_critical_top_gap_endpoint(
        axis: Optional[str] = None,
        session: AsyncSession = Depends(get_session),
    ):
        return await get_axis_critical_top_gap(axis=axis, session=session)

    async def run_test() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE mcp_llm_axis_scores (
                        id INTEGER PRIMARY KEY,
                        server_id VARCHAR,
                        axis_name VARCHAR NOT NULL,
                        label VARCHAR,
                        label_index INTEGER,
                        probs JSON,
                        p_top FLOAT,
                        p_critical FLOAT,
                        p_danger FLOAT,
                        escalated BOOLEAN,
                        escalated_to VARCHAR,
                        scored_at DATETIME
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (
                        id, server_id, axis_name, label, label_index, probs,
                        p_top, p_critical, p_danger, escalated, escalated_to, scored_at
                    ) VALUES (
                        :id, :server_id, :axis_name, :label, :label_index, :probs,
                        :p_top, :p_critical, :p_danger, :escalated, :escalated_to, :scored_at
                    )
                    """
                ),
                [
                    {
                        "id": 1,
                        "server_id": "server-1",
                        "axis_name": "auth_strength",
                        "label": "strong",
                        "label_index": 0,
                        "probs": "{}",
                        "p_top": 0.2,
                        "p_critical": 0.7,
                        "p_danger": 0.1,
                        "escalated": True,
                        "escalated_to": "auth_strength",
                        "scored_at": "2026-01-01T00:00:00",
                    },
                    {
                        "id": 2,
                        "server_id": "server-1",
                        "axis_name": "network_egress",
                        "label": "restricted",
                        "label_index": 0,
                        "probs": "{}",
                        "p_top": 0.8,
                        "p_critical": 0.1,
                        "p_danger": 0.1,
                        "escalated": False,
                        "escalated_to": None,
                        "scored_at": "2026-01-01T00:00:00",
                    },
                    {
                        "id": 3,
                        "server_id": "server-2",
                        "axis_name": "network_egress",
                        "label": "open",
                        "label_index": 1,
                        "probs": "{}",
                        "p_top": 0.6,
                        "p_critical": 0.2,
                        "p_danger": 0.2,
                        "escalated": False,
                        "escalated_to": None,
                        "scored_at": "2026-01-01T00:00:00",
                    },
                ],
            )

        transport = httpx.ASGITransport(app=that_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/axes/critical-top-gap")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["axes"]) == 2
            assert payload["totals"] == {
                "rows": 3,
                "critical_gt_top": 1,
                "escalated": 1,
            }

            by_name = {item["axis_name"]: item for item in payload["axes"]}
            assert by_name["auth_strength"]["rows"] == 1
            assert by_name["auth_strength"]["critical_gt_top"] == 1
            assert by_name["auth_strength"]["escalated"] == 1
            assert by_name["network_egress"]["rows"] == 2

            response = await client.get(
                "/api/axes/critical-top-gap",
                params={"axis": "auth_strength"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["axes"]) == 1
            assert payload["axes"][0]["axis_name"] == "auth_strength"
            assert payload["axes"][0]["rows"] == 1
            assert payload["axes"][0]["critical_gt_top"] == 1
            assert payload["axes"][0]["escalated"] == 1
            assert payload["totals"] == {
                "rows": 1,
                "critical_gt_top": 1,
                "escalated": 1,
            }

        await engine.dispose()

    asyncio.run(run_test())
    print("PASS")