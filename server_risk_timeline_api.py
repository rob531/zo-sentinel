from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import requests
from typing import List, Dict

router = APIRouter()


@router.get("/servers/{server_id}/risk_timeline")
async def read_server_risk_timeline(request: Request, server_id: str) -> JSONResponse:
    query = """
        SELECT created_at, old_tier, new_tier
        FROM perspective_events
        WHERE server_id = ? AND change_type = 'tier_change'
        ORDER BY created_at DESC
        LIMIT 200
    """
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": [server_id]},
    )
    data = resp.json()
    rows: List[Dict] = data.get("rows", [])

    if not rows:
        return JSONResponse(status_code=404, content={"detail": "No risk timeline found"})

    changes = [
        {
            "created_at": row["created_at"],
            "old_tier": row["old_tier"],
            "new_tier": row["new_tier"],
        }
        for row in rows
    ]

    return JSONResponse(
        content={
            "server_id": server_id,
            "changes": changes,
            "count": len(changes),
        }
    )


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ---- Mock write_service ------------------------------------------------
    _mock_db: List[Dict] = []

    class _MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code

        def json(self):
            return self._json

    def _mock_post(url: str, json: Dict) -> _MockResponse:
        sql = json.get("query", "").strip().lower()
        params = json.get("params", [])
        if sql.startswith("insert"):
            # Expected order: server_id, change_type, created_at, old_tier, new_tier
            server_id, change_type, created_at, old_tier, new_tier = params
            _mock_db.append(
                {
                    "server_id": server_id,
                    "change_type": change_type,
                    "created_at": created_at,
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                }
            )
            return _MockResponse({"rows_affected": 1})
        elif sql.startswith("select"):
            # Filter rows
            filtered = [
                {
                    "created_at": row["created_at"],
                    "old_tier": row["old_tier"],
                    "new_tier": row["new_tier"],
                }
                for row in _mock_db
                if row["server_id"] == params[0] and row["change_type"] == "tier_change"
            ]
            # Order by created_at DESC
            filtered.sort(key=lambda r: r["created_at"], reverse=True)
            # Apply limit 200
            filtered = filtered[:200]
            return _MockResponse({"rows": filtered})
        else:
            return _MockResponse({"rows": []})

    # Patch requests.post
    requests.post = _mock_post  # type: ignore

    # ---- Seed data ---------------------------------------------------------
    server_id = "srv-123"
    now = datetime.datetime.utcnow()
    tiers = [("bronze", "silver"), ("silver", "gold"), ("gold", "platinum")]
    for i, (old, new) in enumerate(tiers):
        created_at = (now - datetime.timedelta(minutes=i)).isoformat() + "Z"
        insert_sql = """
            INSERT INTO perspective_events
            (server_id, change_type, created_at, old_tier, new_tier)
            VALUES (?, ?, ?, ?, ?)
        """
        requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": insert_sql,
                "params": [server_id, "tier_change", created_at, old, new],
            },
        )

    # ---- Build app and test -------------------------------------------------
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get(f"/servers/{server_id}/risk_timeline")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    changes = payload.get("changes", [])
    assert len(changes) >= 3, f"Expected at least 3 changes, got {len(changes)}"
    for ch in changes:
        assert "created_at" in ch, "missing created_at"
        assert "old_tier" in ch, "missing old_tier"
        assert "new_tier" in ch, "missing new_tier"
    print("PASS")