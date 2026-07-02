from fastapi import Depends
from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Org, User, OrgMember
import requests

def org_scope(sql, org_id):
    if org_id:
        return sql.where(OrgMember.org_id == org_id)
    return sql

def create_org(name: str, session: Session = Depends(get_session)) -> int:
    org = Org(name=name)
    session.add(org)
    session.commit()
    session.refresh(org)
    return org.id

def add_member(org_id: int, user_id: int, role: str, session: Session = Depends(get_session)):
    member = OrgMember(org_id=org_id, user_id=user_id, role=role)
    session.add(member)
    session.commit()

def list_members(org_id: int, session: Session = Depends(get_session)):
    stmt = select(OrgMember).where(OrgMember.org_id == org_id)
    result = session.execute(stmt)
    return result.scalars().all()

def remove_member(org_id: int, user_id: int, session: Session = Depends(get_session)):
    stmt = select(OrgMember).where(and_(OrgMember.org_id == org_id, OrgMember.user_id == user_id))
    result = session.execute(stmt)
    member = result.scalar_one_or_none()
    if member:
        session.delete(member)
        session.commit()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    from app.dependency_overrides import app
    app.dependency_overrides[get_session] = override_get_session

    org_id = create_org("Test Org")
    add_member(org_id, 1, "admin")

    stmt = select(OrgMember).where(OrgMember.org_id == org_id)
    scoped_stmt = org_scope(stmt, org_id)
    db = next(override_get_session())
    result = db.execute(scoped_stmt)
    members = result.scalars().all()

    assert len(members) == 1
    assert members[0].user_id == 1
    assert members[0].role == "admin"

    print("PASS")