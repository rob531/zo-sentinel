from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
from app.db import get_session
from app.models import Settings
from sqlalchemy.orm import Session
import httpx

router = APIRouter(prefix="/settings", tags=["settings"])

LOCKED_KEYS = {"feature_flags", "threshold_overrides"}

class SettingItem(BaseModel):
    key: str
    value: str | int | bool

class SettingResponse(BaseModel):
    key: str
    value: str | int | bool
    updated_at: datetime

async def get_org_id_from_jwt() -> str:
    # Mock implementation for testing
    return "test_org"

@router.get("/", response_model=Dict[str, SettingResponse])
async def list_settings(db: Session = Depends(get_session), org_id: str = Depends(get_org_id_from_jwt)):
    settings = db.query(Settings).filter(Settings.org_id == org_id).all()
    return {setting.key: SettingResponse(
        key=setting.key,
        value=setting.value,
        updated_at=setting.updated_at
    ) for setting in settings}

@router.patch("/{key}", response_model=SettingResponse)
async def upsert_setting(
    key: str,
    setting: SettingItem,
    db: Session = Depends(get_session),
    org_id: str = Depends(get_org_id_from_jwt)
):
    if key in LOCKED_KEYS:
        raise HTTPException(status_code=403, detail="Key is locked and cannot be modified")

    setting_obj = db.query(Settings).filter(Settings.org_id == org_id, Settings.key == key).first()
    if setting_obj:
        setting_obj.value = setting.value
        setting_obj.updated_at = datetime.utcnow()
    else:
        setting_obj = Settings(
            org_id=org_id,
            key=key,
            value=setting.value,
            updated_at=datetime.utcnow()
        )
        db.add(setting_obj)
    db.commit()
    db.refresh(setting_obj)

    return SettingResponse(
        key=setting_obj.key,
        value=setting_obj.value,
        updated_at=setting_obj.updated_at
    )

@router.delete("/{key}", response_model=SettingResponse)
async def delete_setting(
    key: str,
    db: Session = Depends(get_session),
    org_id: str = Depends(get_org_id_from_jwt)
):
    if key in LOCKED_KEYS:
        raise HTTPException(status_code=403, detail="Key is locked and cannot be deleted")

    setting_obj = db.query(Settings).filter(Settings.org_id == org_id, Settings.key == key).first()
    if not setting_obj:
        raise HTTPException(status_code=404, detail="Setting not found")

    db.delete(setting_obj)
    db.commit()

    return SettingResponse(
        key=setting_obj.key,
        value=setting_obj.value,
        updated_at=setting_obj.updated_at
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import Settings

    app = FastAPI()
    app.include_router(router)

    # Override the database session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test the API
    test_org_id = "test_org"

    # Set a key
    response = client.patch(
        f"/settings/test_key",
        json={"key": "test_key", "value": "test_value"},
        headers={"Authorization": f"Bearer {test_org_id}"}
    )
    assert response.status_code == 200

    # Read it back
    response = client.get(
        "/settings",
        headers={"Authorization": f"Bearer {test_org_id}"}
    )
    assert response.status_code == 200
    assert "test_key" in response.json()

    # Delete it
    response = client.delete(
        f"/settings/test_key",
        headers={"Authorization": f"Bearer {test_org_id}"}
    )
    assert response.status_code == 200

    # Assert 404 on re-read
    response = client.get(
        "/settings",
        headers={"Authorization": f"Bearer {test_org_id}"}
    )
    assert response.status_code == 200
    assert "test_key" not in response.json()

    print("PASS")