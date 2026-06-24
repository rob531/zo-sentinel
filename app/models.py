"""ORM models for the app tier -- the multi-tenant core (orgs/users) + API keys.
Portable column types only (String/DateTime/ForeignKey) so the same DDL runs on
sqlite (dev/CI) and Postgres (deploy).
"""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("orgs.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("orgs.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
