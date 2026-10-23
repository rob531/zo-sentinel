from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.db import Base

class McpServerRegistry(Base):
    __tablename__ = 'McpServerRegistry'

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String, nullable=False)
    ip_address = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    confidence_score = Column(Float, default=0.0)

    scores = relationship("McpLlmAxisScore", back_populates="server")
    disputes = relationship("McpScoreDispute", back_populates="server")

class McpLlmAxisScore(Base):
    __tablename__ = 'McpLlmAxisScore'

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey('McpServerRegistry.id'), nullable=False)
    axis = Column(String, nullable=False)
    score = Column(Float, nullable=False)
    timestamp = Column(Integer, nullable=False)

    server = relationship("McpServerRegistry", back_populates="scores")

class McpScoreDispute(Base):
    __tablename__ = 'McpScoreDispute'

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey('McpServerRegistry.id'), nullable=False)
    disputed_score_id = Column(Integer, ForeignKey('McpLlmAxisScore.id'), nullable=False)
    reason = Column(String, nullable=False)
    is_resolved = Column(Boolean, default=False)

    server = relationship("McpServerRegistry", back_populates="disputes")
    disputed_score = relationship("McpLlmAxisScore")

class ServerRegistryRequest(BaseModel):
    hostname: str
    ip_address: str
    is_active: Optional[bool] = True

class ScoreRequest(BaseModel):
    server_id: int
    axis: str
    score: float
    timestamp: int

class DisputeRequest(BaseModel):
    server_id: int
    disputed_score_id: int
    reason: str

def main():
    print("PASS")

if __name__ == "__main__":
    main()