"""One-time lift of the pre-MongoDB on-disk JSON into the database.

Runs on every startup but writes nothing once the collections hold the data, so
it is safe to leave in place. The source files are renamed rather than deleted:
a migration that eats the only copy of the data is not a migration.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import db
from .config import DATA_DIR

LEGACY_RULES_PATH = DATA_DIR / "rules.json"


def _archive(path: Path) -> None:
    path.rename(path.with_suffix(path.suffix + ".migrated"))


def migrate_rules() -> bool:
    if not LEGACY_RULES_PATH.exists():
        return False
    # An empty rules collection is indistinguishable from "no rules saved", so
    # check for the document, not for a non-empty list.
    if db.settings().find_one({"_id": db.SETTINGS_ID_RULES}):
        _archive(LEGACY_RULES_PATH)
        return False
    try:
        with open(LEGACY_RULES_PATH, "r", encoding="utf-8") as f:
            db.write_rules(json.load(f))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Skipping unreadable {LEGACY_RULES_PATH}: {exc}")
        return False
    _archive(LEGACY_RULES_PATH)
    return True


def migrate_report_metas() -> int:
    """Copy each data/<report_id>/meta.json into the reports collection.

    The Parquet frame beside it stays exactly where it is — only the metadata
    moves. The cached `report` blob is dropped, since it is recomputed on load.
    """
    if not DATA_DIR.exists():
        return 0

    moved = 0
    for report_dir in DATA_DIR.iterdir():
        meta_path = report_dir / "meta.json"
        if not report_dir.is_dir() or not meta_path.exists():
            continue
        if db.reports_meta().find_one({"_id": report_dir.name}):
            _archive(meta_path)
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta.pop("report", None)
            db.write_report_meta(report_dir.name, meta)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Skipping unreadable {meta_path}: {exc}")
            continue
        _archive(meta_path)
        moved += 1
    return moved


def migrate_frames() -> int:
    """Move each data/<report_id>/df.parquet into GridFS, then delete the file.

    The bytes are read back out of GridFS and compared before the only other
    copy is removed. A frame is the irreplaceable artifact here — the report is
    derived from it — so "uploaded without error" is not good enough evidence
    that it landed intact.
    """
    if not DATA_DIR.exists():
        return 0

    moved = 0
    for report_dir in sorted(DATA_DIR.iterdir()):
        frame_path = report_dir / "df.parquet"
        if not report_dir.is_dir() or not frame_path.exists():
            continue

        report_id = report_dir.name
        original = frame_path.read_bytes()

        if db.has_frame(report_id):
            # A previous run uploaded it; only the local copy is left to clear.
            if db.read_frame(report_id) != original:
                print(f"Frame for {report_id} differs from the stored copy — leaving the file in place.")
                continue
        else:
            try:
                db.write_frame(report_id, original)
            except Exception as exc:
                print(f"Could not upload frame for {report_id}, leaving it on disk: {exc}")
                continue
            if db.read_frame(report_id) != original:
                print(f"Frame for {report_id} did not round-trip through GridFS — leaving the file in place.")
                db.delete_frame(report_id)
                continue

        frame_path.unlink()
        moved += 1

    _prune_empty_report_dirs()
    return moved


def _prune_empty_report_dirs() -> None:
    """Remove report directories left holding nothing but `*.migrated` archives."""
    for report_dir in DATA_DIR.iterdir():
        if not report_dir.is_dir():
            continue
        remaining = list(report_dir.iterdir())
        if remaining and all(p.suffix == ".migrated" for p in remaining):
            for archive in remaining:
                archive.unlink()
            remaining = []
        if not remaining:
            report_dir.rmdir()


def run() -> None:
    if migrate_rules():
        print("Migrated categorization rules into MongoDB.")
    count = migrate_report_metas()
    if count:
        print(f"Migrated {count} report(s) into MongoDB.")
    frames = migrate_frames()
    if frames:
        print(f"Migrated {frames} lead frame(s) into GridFS and removed them from disk.")
