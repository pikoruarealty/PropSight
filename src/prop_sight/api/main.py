"""FastAPI app entry point.

Run single-process from the repo root:
    uvicorn prop_sight.api.main:app --reload --port 8010
(REPORTS lives in process memory — never use --workers.)
"""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR
from .routes import pages, reports_api, upload

load_dotenv()

app = FastAPI(title="PropSight")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(pages.router)
app.include_router(upload.router)
app.include_router(reports_api.router)
