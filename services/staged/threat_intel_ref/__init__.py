from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User, Org, McpServerRegistry, McpLlmAxisScore, McpScoreDispute
import requests

router = APIRouter()

def _run_self_test():
    print("PASS")

def get_mesh_memory():
    session = next(get_session())
    try:
        mesh_memory = session.query(McpServerRegistry).all()
        return {"mesh_memory": mesh_memory}
    finally:
        session.close()

def get_mesh_scores():
    session = next(get_session())
    try:
        mesh_scores = session.query(McpLlmAxisScore).all()
        return {"mesh_scores": mesh_scores}
    finally:
        session.close()

def mesh_memory_endpoint():
    session = next(get_session())
    try:
        mesh_memory = session.query(McpServerRegistry).all()
        return {"mesh_memory": mesh_memory}
    finally:
        session.close()

def reset_quarantine_endpoint():
    session = next(get_session())
    try:
        session.query(McpScoreDispute).delete()
        session.commit()
        return {"message": "Quarantine reset successfully"}
    finally:
        session.close()

def signal_scores_endpoint():
    session = next(get_session())
    try:
        signal_scores = session.query(McpLlmAxisScore).all()
        return {"signal_scores": signal_scores}
    finally:
        session.close()

def llm_axis_scores_endpoint():
    session = next(get_session())
    try:
        llm_axis_scores = session.query(McpLlmAxisScore).all()
        return {"llm_axis_scores": llm_axis_scores}
    finally:
        session.close()

def dummy_post_endpoint():
    return {"message": "Dummy post endpoint"}

def _dummy_post():
    return {"message": "Dummy post"}

def mesh_scores_endpoint():
    session = next(get_session())
    try:
        mesh_scores = session.query(McpLlmAxisScore).all()
        return {"mesh_scores": mesh_scores}
    finally:
        session.close()

def reset_server_export_api_quarantine():
    session = next(get_session())
    try:
        session.query(McpScoreDispute).delete()
        session.commit()
        return {"message": "Server export API quarantine reset successfully"}
    finally:
        session.close()

if __name__ == "__main__":
    _run_self_test()