"""Upload orchestration: parse workbooks, detect types, create draft reports."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pandas as pd

from ...ingestion.excel_reader import read_workbook
from ...ingestion.normalize import looks_like_lead_sheet, normalize_headers
from ...ingestion.property_type import PROPERTY_TYPE_OPTIONS, detect_sheet_property_type
from ...ingestion.schema_llm import normalize_with_llm_fallback
from ..state import REPORTS, REPORTS_LOCK

# Upload parsing can take a while (reading each workbook, then a possible LLM
# round-trip per unrecognized sheet) and used to run as one opaque blocking
# request with a single "Parsing…" label the whole time — indistinguishable
# from a hang. PROGRESS lets the upload page poll and show which file/sheet is
# actually being worked on right now.
PROGRESS: dict[str, dict] = {}
PROGRESS_LOCK = threading.RLock()

def _campaign_groups(raw: pd.DataFrame, normalized: pd.DataFrame) -> list[tuple[str | None, pd.Index]]:
    """Split a sheet into one virtual sheet per campaign, or leave it whole.

    `dropna=False` is essential: pandas' default drops every row whose group key
    is NaN, which silently deleted every lead with a blank campaign from the
    upload entirely.
    """
    if "campaign" not in normalized.columns:
        return [(None, raw.index)]

    groups = normalized.groupby("campaign", dropna=False).groups
    return list(groups.items())


def _virtual_sheet_name(sheet_name: str, campaign: object) -> str:
    if campaign is None:
        return sheet_name
    label = "" if pd.isna(campaign) else str(campaign).strip()
    if not label or label.lower() == "nan":
        label = "No campaign"
    return f"{sheet_name} [{label}]"


def _set_progress(progress_id: str | None, **fields) -> None:
    if progress_id is None:
        return
    with PROGRESS_LOCK:
        entry = PROGRESS.setdefault(progress_id, {})
        entry.update(fields)


def create_draft(files: list[tuple[str, bytes]], progress_id: str | None = None) -> dict:
    """Parse uploaded workbooks fully in memory and store a draft report.

    Every sheet of every workbook is inspected independently, since one workbook
    can mix real lead sheets with unrelated/junk sheets. A sheet whose headers
    match too few canonical fields is escalated to the LLM, which may recognize
    an export format the alias table has never seen.

    Returns the confirm-step payload: per-sheet row counts, a lead-likeness flag,
    the auto-detected property type (None forces a manual pick), and any
    LLM-inferred column mapping so the user can see what was guessed.
    """
    parsed: list[dict] = []
    files_summary = []
    total_files = len(files)

    for file_index, (filename, contents) in enumerate(files):
        _set_progress(
            progress_id,
            stage=f"Reading {filename}…",
            file_index=file_index,
            total_files=total_files,
        )
        sheets = read_workbook(contents, filename)
        sheet_entries = []
        total_sheets = len(sheets)

        for sheet_index, (sheet_name, df) in enumerate(sheets.items()):
            _set_progress(
                progress_id,
                stage=f"{filename} — checking sheet “{sheet_name}” ({sheet_index + 1} of {total_sheets})",
                file_index=file_index,
                total_files=total_files,
            )
            normalized = normalize_headers(df)

            groups = _campaign_groups(df, normalized)
            for group_index, (campaign, index) in enumerate(groups):
                # `read_workbook` resets each sheet's index, so group labels and
                # positions coincide; .loc keeps that explicit either way.
                group_df = df.loc[index].reset_index(drop=True)
                if group_df.empty:
                    continue

                virtual_name = _virtual_sheet_name(sheet_name, campaign)
                if len(groups) > 1:
                    _set_progress(
                        progress_id,
                        stage=(
                            f"{filename} — “{sheet_name}”: campaign "
                            f"{group_index + 1} of {len(groups)} ({virtual_name})"
                        ),
                        file_index=file_index,
                        total_files=total_files,
                    )
                _, schema_info = normalize_with_llm_fallback(group_df)
                looks_lead = (
                    looks_like_lead_sheet(normalize_headers(group_df))
                    or schema_info["matched_fields"] >= 5
                )
                detected = detect_sheet_property_type(filename, virtual_name)

                parsed.append(
                    {
                        "filename": filename,
                        "sheet_name": virtual_name,
                        "df": group_df,
                        "include": looks_lead,
                        "property_type": detected,
                        "column_mapping": schema_info["mapping"],
                    }
                )
                sheet_entries.append(
                    {
                        "sheet_index": len(parsed) - 1,
                        "sheet_name": virtual_name,
                        "row_count": len(group_df),
                        "looks_like_lead_sheet": looks_lead,
                        "detected_type": detected,
                        "include": looks_lead,
                        "llm_mapped": schema_info["llm_used"],
                        "column_mapping": schema_info["mapping"],
                    }
                )

        files_summary.append(
            {
                "index": file_index,
                "filename": filename,
                "sheet_count": len(sheets),
                "row_count": sum(len(df) for df in sheets.values()),
                "sheets": sheet_entries,
            }
        )

    report_id = uuid.uuid4().hex
    entry = {
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [f["filename"] for f in files_summary],
        "parsed": parsed,
        "df": None,
        "report": None,
        "insights_cache": None,
        "summaries_cache": None,
        "dedupe_stats": None,
    }
    with REPORTS_LOCK:
        REPORTS[report_id] = entry

    return {
        "report_id": report_id,
        "files": files_summary,
        "property_type_options": PROPERTY_TYPE_OPTIONS,
    }


def start_draft(files: list[tuple[str, bytes]]) -> str:
    """Kick off `create_draft` on a background thread; return a ticket to poll.

    Starlette runs a sync `def` route in a thread-pool worker rather than on
    the event loop, so this thread can run alongside the polling requests
    without blocking them — the whole point, since a single opaque "Parsing…"
    request was indistinguishable from a hang.
    """
    progress_id = uuid.uuid4().hex
    with PROGRESS_LOCK:
        PROGRESS[progress_id] = {"done": False, "stage": "Starting…", "error": None, "result": None}

    def _run() -> None:
        try:
            result = create_draft(files, progress_id=progress_id)
            _set_progress(progress_id, done=True, result=result, stage="Done")
        except Exception as exc:
            _set_progress(progress_id, done=True, error=str(exc), stage="Failed")

    threading.Thread(target=_run, daemon=True).start()
    return progress_id


def get_progress(progress_id: str) -> dict | None:
    with PROGRESS_LOCK:
        entry = PROGRESS.get(progress_id)
        return dict(entry) if entry is not None else None
