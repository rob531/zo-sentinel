from fastapi import FastAPI
import requests
from fastapi.testclient import TestClient

app = FastAPI()

@app.post("/trigger_population")
async def trigger_population():
    try:
        response = requests.post("http://mcp_definition_history_populator_daemon:8000/trigger")
        if response.status_code == 200:
            return {"status": "success", "message": "Triggered population successfully"}
        else:
            return {"status": "error", "message": f"Failed to trigger population: {response.text}"}
    except Exception as e:
        return {"status": "error", "message": f"An error occurred: {str(e)}"}

if __name__ == "__main__":
    client = TestClient(app)
    response = client.post("/trigger_population")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("PASS")