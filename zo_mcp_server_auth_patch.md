import fastapi
from fastapi import FastAPI, HTTPException, Status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from fastapi.requests import Request
import requests
from typing import List

app = FastAPI()

security = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username == "mcp" and form_data.password == "mcp":
        access_token = forms.token
        return {"access_token": access_token}
    else:
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/token")
def create_access_token():
    access_token = requests.post("https://example.com/token").json()["access_token"]
    return {"access_token": access_token}

@app.get("/user")
def get_current_user(token: str = Depends(security)):
    user_id = token.split(".")[0]
    # your logic to verify the user_id goes here
    return {"user_id": user_id}

@app.post("/user")
def create_user(username: str, password: str):
    return {"username": username}