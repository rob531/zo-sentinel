from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import httpx
import asyncio
from app_router_registry import include_app_routers

app = FastAPI()

# Mount static files for UI
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all app routers
include_app_routers(app)

@app.get("/health")
async def health_check():
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get("http://write_service:8772/health")
            response.raise_for_status()
            return {"status": "healthy", "service_health": response.json()}
        except httpx.RequestError as e:
            return {"status": "unhealthy", "error": str(e)}

async def start_uvicorn():
    import uvicorn
    await asyncio.sleep(10)  # Wait 10 seconds before starting uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8790)

if __name__ == "__main__":
    asyncio.run(start_uvicorn())