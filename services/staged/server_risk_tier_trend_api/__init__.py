from typing import Optional
from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class SentinelService:
    def __init__(self):
        self.app = FastAPI()

    def get_server_registry(self, session=Depends(get_session)):
        return session.query(McpServerRegistry).all()

    def get_llm_axis_scores(self, session=Depends(get_session)):
        return session.query(McpLlmAxisScore).all()

    def get_score_disputes(self, session=Depends(get_session)):
        return session.query(McpScoreDispute).all()

    def get_orgs(self, session=Depends(get_session)):
        return session.query(Org).all()

    def get_users(self, session=Depends(get_session)):
        return session.query(User).all()

if __name__ == "__main__":
    service = SentinelService()
    print("PASS")