from collections import Counter
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


router = APIRouter(prefix="/api")


class DuplicateDensityAxis(BaseModel):
    axis_name: str
    rows: int
    distinct_servers: int
    duplicate_groups: int
    servers_with_multiple: int
    duplicate_row_pct: float


class DuplicateDensityOffender(BaseModel):
    server_id: str
    axis_name: str
    rows: int


class DuplicateDensityResponse(BaseModel):
    total_rows: int
    axes: List[DuplicateDensityAxis]
    top_offenders: List[DuplicateDensityOffender]


@router.get(
    "/scores/duplicate-density",
    response_model=DuplicateDensityResponse,
)
def get_duplicate_density(db: Session = Depends(get_session)) -> DuplicateDensityResponse:
    result = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
        )
    ).all()

    total_rows = len(result)
    if total_rows == 0:
        return DuplicateDensityResponse(
            total_rows=0,
            axes=[],
            top_offenders=[],
        )

    axis_rows: Dict[str, int] = {}
    axis_servers: Dict[str, set] = {}
    group_counts: Counter = Counter()

    for server_id, axis_name in result:
        axis_rows[axis_name] = axis_rows.get(axis_name, 0) + 1
        axis_servers.setdefault(axis_name, set()).add(server_id)
        group_counts[(axis_name, server_id)] += 1

    axes: List[DuplicateDensityAxis] = []
    offenders: List[DuplicateDensityOffender] = []

    for axis_name, rows in axis_rows.items():
        duplicate_groups = sum(
            1
            for (axis, _server_id), count in group_counts.items()
            if axis == axis_name and count > 1
        )
        duplicate_rows = sum(
            count - 1
            for (axis, _server_id), count in group_counts.items()
            if axis == axis_name and count > 1
        )
        axes.append(
            DuplicateDensityAxis(
                axis_name=axis_name,
                rows=rows,
                distinct_servers=len(axis_servers[axis_name]),
                duplicate_groups=duplicate_groups,
                servers_with_multiple=duplicate_groups,
                duplicate_row_pct=round((duplicate_rows / rows) * 100.0, 2),
            )
        )

        for (axis, server_id), count in group_counts.items():
            if axis == axis_name and count > 1:
                offenders.append(
                    DuplicateDensityOffender(
                        server_id=server_id,
                        axis_name=axis,
                        rows=count,
                    )
                )

    axes.sort(key=lambda item: item.axis_name)
    offenders.sort(key=lambda item: (-item.rows, item.axis_name, item.server_id))

    return DuplicateDensityResponse(
        total_rows=total_rows,
        axes=axes,
        top_offenders=offenders,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    model_version VARCHAR NOT NULL,
                    p_top FLOAT
                )
                """
            )
        )
        rows = [
            (1, "server-a", "security", "v1", 0.9),
            (2, "server-b", "security", "v1", 0.8),
            (3, "server-c", "security", "v1", 0.7),
            (4, "server-a", "quality", "v1", 0.9),
            (5, "server-b", "quality", "v1", 0.8),
            (6, "server-c", "quality", "v1", 0.7),
            (7, "server-a", "security", "v1", 0.6),
        ]
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, model_version, p_top)
                VALUES
                    (:id, :server_id, :axis_name, :model_version, :p_top)
                """
            ),
            [
                {
                    "id": row[0],
                    "server_id": row[1],
                    "axis_name": row[2],
                    "model_version": row[3],
                    "p_top": row[4],
                }
                for row in rows
            ],
        )

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/scores/duplicate-density")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 7
    assert len(payload["axes"]) == 2
    security = next(axis for axis in payload["axes"] if axis["axis_name"] == "security")
    assert security["duplicate_groups"] == 1
    assert any(
        offender["server_id"] == "server-a"
        and offender["axis_name"] == "security"
        for offender in payload["top_offenders"]
    )
    print("PASS")