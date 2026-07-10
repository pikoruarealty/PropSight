"""App configuration: paths, caps, database and auth settings."""

from __future__ import annotations

import os
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = API_DIR / "templates"
STATIC_DIR = API_DIR / "static"

# Nothing durable lives here any more — users, rules, report metadata and the
# Parquet lead frames are all in MongoDB. The directory is still resolved so the
# one-time migration in `migrate.py` can find and clear a pre-MongoDB install.
DATA_DIR = API_DIR.parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "propsight")

# Rotating this invalidates every issued token, which is the intended way to
# force a global logout. A generated default would do that on every restart.
# At least 32 bytes: HS256 keys shorter than the hash output weaken the MAC.
JWT_SECRET = os.getenv("JWT_SECRET", "propsight-development-secret-change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "12"))
AUTH_COOKIE = "propsight_token"

# The account that must exist for the tool to be usable on a fresh database.
# Seeded once; a later password change is never overwritten.
BOOTSTRAP_ADMIN_USERNAME = os.getenv("PROPSIGHT_ADMIN_USER", "PIKORUA")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("PROPSIGHT_ADMIN_PASSWORD", "Pikorua@123")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # per file
MAX_FILES_PER_UPLOAD = 10
MAX_REPORTS = 20  # (No longer strictly enforced since storage is persistent, kept for backward compat config)
