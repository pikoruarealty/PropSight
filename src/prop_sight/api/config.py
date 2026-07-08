"""App configuration: paths and caps. No persistence paths — everything is in memory."""

from __future__ import annotations

from pathlib import Path

API_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = API_DIR / "templates"
STATIC_DIR = API_DIR / "static"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # per file
MAX_FILES_PER_UPLOAD = 10
MAX_REPORTS = 20  # soft cap on concurrent in-memory reports; oldest evicted
