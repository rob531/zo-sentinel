from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Org

def get_orgs(db: Session = Depends(get_session)) -> list[Org]:
    return db.query(Org).all()

def get_org_by_id(org_id: int, db: Session = Depends(get_session)) -> Org | None:
    return db.query(Org).filter(Org.id == org_id).first()

def get_org_by_name(name: str, db: Session = Depends(get_session)) -> Org | None:
    return db.query(Org).filter(Org.name == name).first()

if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    def test():
        orgs = get_orgs()
        if not orgs:
            return "PASS"
        return "FAIL"

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)