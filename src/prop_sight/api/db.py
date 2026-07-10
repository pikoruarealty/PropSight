"""MongoDB access: users, report metadata, and the global categorization rules.

The client is created lazily on first use rather than at import so that tooling
which imports the package (tests, the analytics layer, `--help`) does not need a
reachable database.
"""

from __future__ import annotations

from typing import Any

import gridfs
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError

from .config import MONGODB_DB, MONGODB_URI

_client: MongoClient | None = None

# Single document, `_id: "rules"`. Rules are global — they describe what a good
# lead means to this business, which does not change per upload or per user.
SETTINGS_ID_RULES = "rules"

# Lead frames are stored as Parquet bytes in GridFS rather than as a binary field
# on the report document: a single upload is capped at 25 MB across 10 files, so
# a frame can outgrow Mongo's 16 MB per-document limit. GridFS chunks instead.
FRAMES_BUCKET = "frames"


class DatabaseUnavailable(RuntimeError):
    """Raised when MongoDB cannot be reached, with the operator-facing fix."""


def client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000, tz_aware=True)
    return _client


def db() -> Database:
    return client()[MONGODB_DB]


def users() -> Collection:
    return db()["users"]


def reports_meta() -> Collection:
    return db()["reports"]


def settings() -> Collection:
    return db()["settings"]


def frames() -> gridfs.GridFSBucket:
    return gridfs.GridFSBucket(db(), bucket_name=FRAMES_BUCKET)


def ping() -> None:
    """Fail loudly, once, at startup rather than on the first user's page load."""
    try:
        client().admin.command("ping")
    except PyMongoError as exc:
        raise DatabaseUnavailable(
            f"Cannot reach MongoDB at {MONGODB_URI}. Start a local mongod, or set "
            f"MONGODB_URI to an Atlas connection string. ({exc})"
        ) from exc


def init_indexes() -> None:
    users().create_index([("username_lower", ASCENDING)], unique=True)
    reports_meta().create_index([("created_at", ASCENDING)])
    # Frames are looked up by report id on every startup.
    db()[f"{FRAMES_BUCKET}.files"].create_index([("filename", ASCENDING)])


# --- Lead frames (Parquet bytes in GridFS) ---------------------------------


def write_frame(report_id: str, data: bytes) -> None:
    """Replace the stored frame for a report.

    The new revision is uploaded before the old one is dropped: GridFS has no
    atomic replace, and losing the frame in the window between a delete and a
    failed upload would lose the only copy of the leads.
    """
    bucket = frames()
    stale = [f._id for f in bucket.find({"filename": report_id})]
    bucket.upload_from_stream(report_id, data)
    for file_id in stale:
        bucket.delete(file_id)


def read_frame(report_id: str) -> bytes | None:
    bucket = frames()
    # A replace that crashed mid-way could leave two revisions; the newest wins.
    newest = None
    for f in bucket.find({"filename": report_id}):
        if newest is None or f.upload_date > newest.upload_date:
            newest = f
    if newest is None:
        return None
    return bucket.open_download_stream(newest._id).read()


def delete_frame(report_id: str) -> None:
    bucket = frames()
    for f in bucket.find({"filename": report_id}):
        bucket.delete(f._id)


def has_frame(report_id: str) -> bool:
    return db()[f"{FRAMES_BUCKET}.files"].count_documents({"filename": report_id}, limit=1) > 0


# --- Rules -----------------------------------------------------------------


def read_rules() -> list[dict[str, Any]]:
    doc = settings().find_one({"_id": SETTINGS_ID_RULES})
    return list(doc.get("rules", [])) if doc else []


def write_rules(rules: list[dict[str, Any]]) -> None:
    settings().update_one(
        {"_id": SETTINGS_ID_RULES}, {"$set": {"rules": rules}}, upsert=True
    )


# --- Report metadata -------------------------------------------------------


def read_report_metas() -> list[dict[str, Any]]:
    return list(reports_meta().find({}))


def write_report_meta(report_id: str, meta: dict[str, Any]) -> None:
    reports_meta().replace_one({"_id": report_id}, {"_id": report_id, **meta}, upsert=True)


def delete_report_meta(report_id: str) -> None:
    reports_meta().delete_one({"_id": report_id})
