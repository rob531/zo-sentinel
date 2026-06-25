from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch
import exemption_expirer

app = FastAPI()

@app.post("/exemptions/expire")
async def expire_exemptions():
    try:
        count = exemption_expirer.expire_exemptions()
        return {"status": "success", "expired_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    client = TestClient(app)

    with patch('exemption_expirer.expire_exemptions') as mock_expire:
        mock_expire.return_value = 5
        response = client.post("/exemptions/expire")

        assert response.status_code == 200
        assert response.json() == {"status": "success", "expired_count": 5}
        mock_expire.assert_called_once()