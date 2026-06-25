from fastapi import FastAPI, APIRouter
from pydantic import BaseModel
from typing import Dict, Optional
import asyncpg
from fastapi.testclient import TestClient

app = FastAPI()
router = APIRouter()

class ServiceHealth(BaseModel):
    last_heartbeat: str
    status: str
    meta: Optional[Dict] = None

@router.get("/system_health", response_model=Dict[str, ServiceHealth])
async def get_system_health():
    query = """
    SELECT
        service_name,
        last_heartbeat,
        status,
        meta
    FROM service_health
    """
    async with app.state.db.acquire() as conn:
        records = await conn.fetch(query)
        return {record['service_name']: ServiceHealth(**record) for record in records}

app.include_router(router)

async def get_application():
    app.state.db = await asyncpg.create_pool(
        database="test",
        user="postgres",
        password="postgres",
        host="localhost",
        port=5432
    )
    return app

if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timedelta

    async def seed_db():
        async with app.state.db.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS service_health (
                service_name VARCHAR PRIMARY KEY,
                last_heartbeat TIMESTAMP NOT NULL,
                status VARCHAR NOT NULL,
                meta JSONB
            )
            """)
            await conn.execute("""
            INSERT INTO service_health (service_name, last_heartbeat, status, meta)
            VALUES
                ('write_service', %s, 'ok', %s),
                ('inference_router', %s, 'stale', %s),
                ('manager_agent', %s, 'ok', %s)
            """, (
                datetime.now(),
                '{"version": "1.0"}',
                datetime.now() - timedelta(minutes=10),
                '{"version": "2.1"}',
                datetime.now(),
                '{"version": "3.0"}'
            ))

    async def main():
        app = await get_application()
        await seed_db()
        client = TestClient(app)
        response = client.get("/system_health")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3
        assert "write_service" in data
        assert "inference_router" in data
        assert "manager_agent" in data
        assert data["inference_router"]["status"] == "stale"
        assert data["write_service"]["status"] == "ok"
        print("PASS")

    asyncio.run(main())