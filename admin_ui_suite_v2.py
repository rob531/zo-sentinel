import os
import datetime
import shutil
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any

# --- Configuration and File Paths ---
ZO_SENTINEL_ROOT = "/home/workspace/zo_sentinel"
STATIC_DIR = os.path.join(ZO_SENTINEL_ROOT, "static", "admin")
ADMIN_EXEMPTIONS_HTML = os.path.join(STATIC_DIR, "admin_exemptions.html")
ADMIN_POLICIES_HTML = os.path.join(STATIC_DIR, "admin_policies.html")
ADMIN_SUBMISSIONS_HTML = os.path.join(STATIC_DIR, "admin_submissions.html")
ADMIN_UI_SUITE_PY = os.path.join(ZO_SENTINEL_ROOT, "admin_ui_suite_v2.py")

# Ensure directories exist
os.makedirs(STATIC_DIR, exist_ok=True)

# --- Backup Function ---
def backup_file(filepath):
    """Backs up a given file to a .bak.<UTC> file."""
    if os.path.exists(filepath):
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
        backup_filepath = f"{filepath}.bak.{timestamp}"