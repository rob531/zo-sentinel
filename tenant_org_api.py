from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List
from tenant_org_model import TenantOrg, TenantOrgCreate, TenantOrgUpdate

router = APIRouter(prefix="/organizations", tags=["tenant_organizations"])

class WriteService:
    def __init__(self):
        self.organizations = {}

    def create_org(self, org: TenantOrgCreate) -> TenantOrg:
        org_id = str(len(self.organizations) + 1)
        org_dict = org.dict()
        org_dict["id"] = org_id
        org_dict["is_deleted"] = False
        self.organizations[org_id] = org_dict
        return TenantOrg(**org_dict)

    def get_org(self, org_id: str) -> Optional[TenantOrg]:
        org_data = self.organizations.get(org_id)
        if org_data and not org_data["is_deleted"]:
            return TenantOrg(**org_data)
        return None

    def get_orgs(self) -> List[TenantOrg]:
        return [TenantOrg(**org) for org in self.organizations.values() if not org["is_deleted"]]

    def update_org(self, org_id: str, org: TenantOrgUpdate) -> Optional[TenantOrg]:
        org_data = self.organizations.get(org_id)
        if org_data and not org_data["is_deleted"]:
            org_data.update(org.dict(exclude_unset=True))
            return TenantOrg(**org_data)
        return None

    def delete_org(self, org_id: str) -> bool:
        org_data = self.organizations.get(org_id)
        if org_data and not org_data["is_deleted"]:
            org_data["is_deleted"] = True
            return True
        return False

write_service = WriteService()

@router.post("/", response_model=TenantOrg, status_code=status.HTTP_201_CREATED)
async def create_organization(org: TenantOrgCreate):
    return write_service.create_org(org)

@router.get("/", response_model=List[TenantOrg])
async def get_organizations():
    return write_service.get_orgs()

@router.get("/{org_id}", response_model=TenantOrg)
async def get_organization(org_id: str):
    org = write_service.get_org(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org

@router.put("/{org_id}", response_model=TenantOrg)
async def update_organization(org_id: str, org: TenantOrgUpdate):
    updated_org = write_service.update_org(org_id, org)
    if updated_org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return updated_org

@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(org_id: str):
    if not write_service.delete_org(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test data
    test_org = TenantOrgCreate(name="Test Org", description="Test Description")

    # Test CREATE
    response = client.post("/", json=test_org.dict())
    assert response.status_code == 201
    org_id = response.json()["id"]

    # Test READ (single)
    response = client.get(f"/{org_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Test Org"

    # Test READ (all)
    response = client.get("/")
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Test UPDATE
    update_data = TenantOrgUpdate(name="Updated Org")
    response = client.put(f"/{org_id}", json=update_data.dict())
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Org"

    # Test DELETE
    response = client.delete(f"/{org_id}")
    assert response.status_code == 204

    # Verify DELETE
    response = client.get(f"/{org_id}")
    assert response.status_code == 404

    print("PASS")