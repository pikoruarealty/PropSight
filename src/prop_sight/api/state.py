"""Persistent report store plus the combined all-leads pool.

Everything lives in MongoDB: report metadata as documents, and the lead frames
as Parquet blobs in a GridFS bucket. Nothing durable is kept on the filesystem.
"""

from __future__ import annotations

import io
import shutil
import threading

import pandas as pd

from ..ingestion.dedupe import dedupe_leads
from ..ingestion.mojibake import is_text_column
from . import db
from .config import DATA_DIR

# report_id -> {status, created_at, files, parsed, df, report, insights_cache, ...}
REPORTS: dict[str, dict] = {}
REPORTS_LOCK = threading.RLock()

# The virtual report id for "every lead from every upload, deduplicated".
COMBINED_ID = "all"

# Built lazily from every ready report and invalidated whenever one is added or
# removed, so the (expensive) cross-file dedupe runs once per change, not per
# page load or filter click.
_COMBINED: dict | None = None


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Make a merged frame writable to Parquet.

    Different exports type the same field differently — `email` is text in one
    sheet and an Excel error code read as int in another. Concatenating gives an
    object column of mixed Python types, which Arrow refuses to serialize. Every
    such column is text as far as the analytics layer is concerned, so cast to
    pandas' nullable string dtype (which preserves <NA>, unlike `astype(str)`
    turning missing values into the literal "nan").
    """
    out = df.rename(columns=str).copy()
    for col in out.columns:
        if is_text_column(out[col]):
            out[col] = out[col].astype("string")
    return out


def _blank_entry(status: str = "draft") -> dict:
    return {
        "status": status,
        "created_at": "",
        "files": [],
        "parsed": [],
        "df": None,
        "report": None,
        "insights_cache": None,
        "summaries_cache": None,
        "dedupe_stats": None,
    }


def current_rules() -> list[dict]:
    """The saved categorization rules. Imported lazily to keep this module low-level."""
    from .services.rules_service import load_rules

    return load_rules()


def invalidate_combined() -> None:
    """Drop the cached combined pool; the next read rebuilds it."""
    global _COMBINED
    with REPORTS_LOCK:
        _COMBINED = None


def rebuild_reports() -> None:
    """Recompute every cached report against the current rules.

    Called after the rules change. The cached `report` blob carries the
    classification, so leaving it in place would show the old verdicts on the
    dashboard while the sorter showed the new ones.
    """
    from ..analytics.report import full_report

    with REPORTS_LOCK:
        rules = current_rules()
        for _, entry in ready_reports():
            entry["report"] = full_report(entry["df"], rules)
            entry["insights_cache"] = None
            entry["summaries_cache"] = None
    invalidate_combined()


def ready_reports() -> list[tuple[str, dict]]:
    with REPORTS_LOCK:
        return [
            (rid, entry)
            for rid, entry in REPORTS.items()
            if entry.get("status") == "ready" and entry.get("df") is not None
        ]


def combined_entry() -> dict | None:
    """One deduplicated DataFrame spanning every confirmed upload.

    Uploads are different formats of the same lead book, not separate datasets,
    so the default view is their union. Deduplication runs across the union
    rather than per file — that is the only place a lead present in both the
    legacy sheet and Privyr can actually be recognized as one lead.

    Returns None when nothing has been confirmed yet.
    """
    global _COMBINED
    with REPORTS_LOCK:
        if _COMBINED is not None:
            return _COMBINED

        ready = ready_reports()
        if not ready:
            return None

        merged = pd.concat(
            [entry["df"] for _, entry in ready], ignore_index=True, sort=False
        )
        deduped, stats = dedupe_leads(merged)

        # Each member frame was already deduplicated on its own upload, so this
        # pass only ever finds leads shared *between* files. Roll the per-upload
        # counts in, or the banner would claim the raw uploads were almost clean.
        for _, entry in ready:
            member = entry.get("dedupe_stats") or {}
            stats["input_rows"] += member.get("input_rows", 0) - member.get("unique_leads", 0)
            stats["duplicate_rows_merged"] += member.get("duplicate_rows_merged", 0)
            stats["invalid_phone_dropped"] += member.get("invalid_phone_dropped", 0)
            stats["missing_phone_dropped"] += member.get("missing_phone_dropped", 0)

        # Built here rather than at import to avoid a circular import: the
        # analytics layer imports nothing from the API layer.
        from ..analytics.report import full_report

        rules = current_rules()
        _COMBINED = {
            "status": "ready",
            "created_at": min(entry["created_at"] for _, entry in ready),
            "files": sorted({f for _, entry in ready for f in entry["files"]}),
            "parsed": [],
            "df": deduped,
            "report": full_report(deduped, rules),
            "insights_cache": None,
            "summaries_cache": None,
            "dedupe_stats": stats,
        }
        return _COMBINED


def _rehydrate(entry: dict) -> dict:
    """Bring a report saved by an older build up to the current schema.

    The cached `report` blob on disk is whatever `full_report` produced at save
    time. Loading it verbatim after the analytics layer has grown a section
    (availability flags, timing, data quality) yields a report the dashboard
    cannot render — it would hide every card whose flag is absent. The frame is
    the durable artifact; the report is derived, so recompute it.

    Frames saved before deduplication existed are deduplicated here too, so an
    old upload is not double-counted in the combined pool.
    """
    from ..analytics.report import full_report
    from ..ingestion.dedupe import PHONE_NORM

    df = entry.get("df")
    if df is None:
        return entry

    if PHONE_NORM not in df.columns:
        df, stats = dedupe_leads(df)
        entry["df"] = df
        entry["dedupe_stats"] = stats

    entry["report"] = full_report(df, current_rules())
    entry["insights_cache"] = None
    entry["summaries_cache"] = None
    return entry


def frame_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a lead frame the way it is stored: Parquet, in memory."""
    buffer = io.BytesIO()
    _parquet_safe(df).to_parquet(buffer)
    return buffer.getvalue()


def load_all_reports() -> None:
    """Load every confirmed report — metadata and frame both from MongoDB."""
    with REPORTS_LOCK:
        for meta in db.read_report_metas():
            report_id = meta.pop("_id")
            try:
                entry = _blank_entry() | meta
                blob = db.read_frame(report_id)
                entry["df"] = pd.read_parquet(io.BytesIO(blob)) if blob else None
                entry["parsed"] = []  # Drafts aren't persisted.
                REPORTS[report_id] = _rehydrate(entry)
            except Exception as exc:
                print(f"Failed to load report {report_id}: {exc}")
    invalidate_combined()


def save_report(report_id: str) -> bool:
    """Persist a confirmed report: metadata and Parquet frame, both to MongoDB.

    Returns whether it was stored. The report is already built and usable in
    memory by the time this runs, so a persistence failure (a missing Parquet
    engine, a dropped connection) must not fail the user's upload — it costs
    them the report across a restart, not the report.
    """
    try:
        return _save_report(report_id)
    except Exception as exc:
        print(f"Failed to persist report {report_id}: {exc}")
        invalidate_combined()
        return False


def _save_report(report_id: str) -> bool:
    with REPORTS_LOCK:
        entry = REPORTS.get(report_id)
        if not entry or entry.get("status") != "ready":
            return False

        # The frame is written first: metadata pointing at a frame that does not
        # exist yet is the one ordering that loses data on a crash between them.
        df = entry.get("df")
        if df is not None:
            db.write_frame(report_id, frame_to_parquet_bytes(df))

        # The `report` blob is deliberately not stored: `_rehydrate` recomputes it
        # from the frame on every load anyway, and its nested keys are lead values
        # ("1.2-2Cr") that Mongo would have to escape.
        db.write_report_meta(
            report_id,
            {
                "status": entry["status"],
                "created_at": entry["created_at"],
                "files": entry["files"],
                "dedupe_stats": entry.get("dedupe_stats"),
            },
        )

    invalidate_combined()
    return True


def delete_report(report_id: str) -> None:
    db.delete_report_meta(report_id)
    db.delete_frame(report_id)
    # Pre-MongoDB builds kept the frame here. Harmless once migrated, but a
    # discarded report must not leave real customer records on the filesystem.
    report_dir = DATA_DIR / report_id
    if report_dir.exists():
        shutil.rmtree(report_dir, ignore_errors=True)
    invalidate_combined()


# Old name, kept because `report_service` and any operator scripts call it.
delete_report_from_disk = delete_report
