from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import Depends
from app.db import get_session
from app.models import VulnAdvisory

class FacetFilters(BaseModel):
    facet_filters: Dict[str, List[str]]

class CompiledFacet(BaseModel):
    sql_where: str
    params: List[Optional[str]]

def compile_facet_filters(facet_filters: FacetFilters) -> CompiledFacet:
    where_clauses = []
    params = []

    for field, values in facet_filters.facet_filters.items():
        if field == "cve_id":
            placeholders = ",".join(["%s"] * len(values))
            where_clauses.append(f"cve_id IN ({placeholders})")
            params.extend(values)
        elif field == "severity":
            placeholders = ",".join(["%s"] * len(values))
            where_clauses.append(f"severity IN ({placeholders})")
            params.extend(values)
        elif field == "published_date":
            where_clauses.append("published_date IS NOT NULL")
        elif field == "modified_date":
            where_clauses.append("modified_date IS NOT NULL")
        elif field == "description":
            where_clauses.append("description IS NOT NULL")
        elif field == "references":
            where_clauses.append("references IS NOT NULL")

    sql_where = " AND ".join(where_clauses) if where_clauses else "1=1"
    return CompiledFacet(sql_where=sql_where, params=params)

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            "input": {"facet_filters": {"cve_id": ["CVE-2021-1234", "CVE-2021-5678"]}},
            "expected": {
                "sql_where": "cve_id IN (%s,%s)",
                "params": ["CVE-2021-1234", "CVE-2021-5678"]
            }
        },
        {
            "input": {"facet_filters": {"severity": ["High", "Critical"]}},
            "expected": {
                "sql_where": "severity IN (%s,%s)",
                "params": ["High", "Critical"]
            }
        },
        {
            "input": {"facet_filters": {"published_date": ["2021-01-01"], "description": [""]}},
            "expected": {
                "sql_where": "published_date IS NOT NULL AND description IS NOT NULL",
                "params": []
            }
        }
    ]

    for test in test_cases:
        result = compile_facet_filters(FacetFilters(**test["input"]))
        assert result.sql_where == test["expected"]["sql_where"]
        assert result.params == test["expected"]["params"]

    print("PASS")