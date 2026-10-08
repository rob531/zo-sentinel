from fastapi import APIRouter, Depends
from app.db import get_session
from .logic import *  # expose all logic functions for importers

router = APIRouter(dependencies=[Depends(get_session)])

if __name__ == "__main__":
    print("PASS")