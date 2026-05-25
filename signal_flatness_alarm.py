import os
from fastapi import FastAPI, Request
from requests import Session
import logging
from typing import List
import pydantic as pd
from datetime import datetime, timedelta
from io import StringIO

app = FastAPI()

logging.basicConfig(filename='main.log', level=logging.INFO)

async def run():
    await app.main()

if __name__ == "__main__":
    run()