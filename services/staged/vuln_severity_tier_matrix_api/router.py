services/_exemplar/router.py
from fastapi import APIRouter

from .logic import get_service_data

router = APIRouter()


@router.get("/")
async def read_service_data():
    return get_service_data()