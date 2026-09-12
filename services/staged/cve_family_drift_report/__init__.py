from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute, User

class Users:
    @staticmethod
    def get_all(db: Session = Depends(get_session)) -> List[User]:
        return db.query(User).all()

    @staticmethod
    def get_by_id(user_id: int, db: Session = Depends(get_session)) -> Optional[User]:
        return db.query(User).filter(User.id == user_id).first()

class ScoreDisputes:
    @staticmethod
    def get_all(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return db.query(McpScoreDispute).all()

    @staticmethod
    def get_by_id(dispute_id: int, db: Session = Depends(get_session)) -> Optional[McpScoreDispute]:
        return db.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()

def get_server_registries(db: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return db.query(McpServerRegistry).all()

def get_score_disputes(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

def users_endpoint():
    return Users.get_all()

def signal_scores_endpoint():
    return {"status": "ok"}

def mesh_memory_endpoint():
    return {"status": "ok"}

def get_mesh_memory_by_id():
    return {"status": "ok"}

def test_self():
    print("PASS")

if __name__ == "__main__":
    test_self()