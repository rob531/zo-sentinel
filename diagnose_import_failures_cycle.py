import os
from pathlib import Path
import logging
from fastapi import FastAPI, HTTPException

app = FastAPI()

logging.basicConfig(level=logging.INFO)

def run():
    try:
        # Check for import failures
        diagnose_failed_import_modules()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    run()