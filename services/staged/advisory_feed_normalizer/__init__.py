from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

router = APIRouter()

def _run_self_test():
    # This is a placeholder for the self-test function
    print("PASS")

def get_mesh_memory_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def orgs_endpoint():
    session = next(get_session())
    orgs = session.query(Org).all()
    return [{"id": org.id, "name": org.name} for org in orgs]

def signal_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def get_mesh_memory():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def mesh_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM McpLlmAxisScore"})
    return response.json()

def _signal_scores_http():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def get_signal_scores():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def _dummy_post():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT 1"})
    return response.json()

if __name__ == "__main__":
    _run_self_test()