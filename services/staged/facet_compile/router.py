from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from app.db import get_session
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/facet", tags=["facet_compile"])

class FacetFilters(BaseModel):
    facet_filters: Dict[str, Any]

class CompiledFacet(BaseModel):
    sql_where: str
    params: List[Any]

def compile_facet_to_where(facet_filters: Dict[str, Any]) -> CompiledFacet:
    where_clauses = []
    params = []

    for column, filters in facet_filters.items():
        if not filters:
            continue

        if isinstance(filters, dict):
            for operator, value in filters.items():
                if operator == "eq":
                    where_clauses.append(f"{column} = ?")
                    params.append(value)
                elif operator == "ne":
                    where_clauses.append(f"{column} != ?")
                    params.append(value)
                elif operator == "gt":
                    where_clauses.append(f"{column} > ?")
                    params.append(value)
                elif operator == "lt":
                    where_clauses.append(f"{column} < ?")
                    params.append(value)
                elif operator == "in":
                    placeholders = ", ".join(["?"] * len(value))
                    where_clauses.append(f"{column} IN ({placeholders})")
                    params.extend(value)
                elif operator == "not_in":
                    placeholders = ", ".join(["?"] * len(value))
                    where_clauses.append(f"{column} NOT IN ({placeholders})")
                    params.extend(value)
        elif isinstance(filters, list):
            placeholders = ", ".join(["?"] * len(filters))
            where_clauses.append(f"{column} IN ({placeholders})")
            params.extend(filters)

    sql_where = " AND ".join(where_clauses) if where_clauses else "1=1"
    return CompiledFacet(sql_where=sql_where, params=params)

@router.post("/compile", response_model=CompiledFacet)
async def compile_facet(facet_filters: FacetFilters, db: Session = Depends(get_session)):
    return compile_facet_to_where(facet_filters.facet_filters)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    test_cases = [
        (
            {"facet_filters": {"column1": {"eq": 1}, "column2": {"gt": 10}}},
            {"sql_where": "column1 = ? AND column2 > ?", "params": [1, 10]}
        ),
        (
            {"facet_filters": {"column1": {"in": [1, 2, 3]}, "column2": {"not_in": [4, 5]}}},
            {"sql_where": "column1 IN (?, ?, ?) AND column2 NOT IN (?, ?)", "params": [1, 2, 3, 4, 5]}
        ),
        (
            {"facet_filters": {"column1": [1, 2, 3], "column2": {"lt": 10}}},
            {"sql_where": "column1 IN (?, ?, ?) AND column2 < ?", "params": [1, 2, 3, 10]}
        )
    ]

    for test_input, expected_output in test_cases:
        response = client.post("/api/facet/compile", json=test_input)
        assert response.status_code == 200
        assert response.json() == expected_output

    print("PASS")