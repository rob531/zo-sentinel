import fastapi
from fastapi import HTTPException, status
from typing import List, Dict
import requests
from datetime import datetime, timedelta

app = fastapi.FastAPI()

class Verdict:
    HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
    KNOWN_THREAT = "KNOWN_THREAT"

@app.post("/github-pr-verdict")
async def github_pr_verdict(github_pr_id: str, commit_message: str):
    url = f"http://127.0.0.1:8772/write"
    payload = {"table": "github_prs", "rows": {"id": github_pr_id, "commit_message": commit_message}}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    if not response.ok:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to query verdict")

    payload = {"table": "verdicts", "rows": {"id": github_pr_id}}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        data = response.json()
        if data["rows"][0]["value"] == Verdict.HIGH_RISK_ISOLATED or data["rows"][0]["value"] == Verdict.KNOWN_THREAT:
            return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="PR check failed due to high risk")
    return {"status": "OK"}

def cycle():
    url = "http://127.0.0.1:8773/inference_router"
    payload = {}
    response = requests.post(url, json=payload)
    if not response.ok:
        print("Failed to query inference")

@app.on_event("shutdown")
async def shutdown_event():
    await app.shutdown()

def run():
    url = "http://127.0.0.1:8773/inference_router"
    payload = {}
    response = requests.post(url, json=payload)
    if not response.ok:
        print("Failed to query inference")

if __name__ == "__main__":
    import logging
    from logging.handlers import RotatingFileHandler

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = RotatingFileHandler('app.log', maxBytes=1024*1024*50, backupCount=5)
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    import uvicorn
    uvicorn.run(app="github_pr_webhook_receiver:app", host="0.0.0.0", port=8773)