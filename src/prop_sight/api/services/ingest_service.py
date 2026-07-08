"""Upload orchestration: parse workbooks, detect types, create draft reports."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pandas as pd

from ...ingestion.excel_reader import read_workbook
from ...ingestion.normalize import looks_like_lead_sheet, normalize_headers
from ...ingestion.property_type import PROPERTY_TYPE_OPTIONS, detect_sheet_property_type
from ..config import MAX_REPORTS
from ..state import REPORTS, REPORTS_LOCK


def create_draft(files: list[tuple[str, bytes]]) -> dict:
    """Parse uploaded workbooks fully in memory and store a draft report.

    Every sheet of every workbook is inspected independently, since one
    workbook can mix real lead sheets with unrelated/junk sheets. Returns
    the confirm-step payload: per-sheet row counts, a lead-likeness flag
    (from header-matching), and the auto-detected property type (None when
    nothing matched, which forces a manual selection in the UI). Sheets
    that don't look like lead data default to excluded.
    """
    parsed = []
    files_summary = []
    for file_index, (filename, contents) in enumerate(files):
        sheets = read_workbook(contents, filename)
        sheet_entries = []
        for sheet_index, (sheet_name, df) in enumerate(sheets.items()):
            df_norm = normalize_headers(df)
            
            # If a campaign column exists, split the dataframe into "virtual sheets"
            if "campaign" in df_norm.columns:
                groups = list(df_norm.groupby("campaign").groups.items())
            else:
                groups = [(None, df.index)]

            for val, idx in groups:
                # Subset the original raw dataframe
                group_df = df.iloc[idx].reset_index(drop=True)
                
                # Name the virtual sheet
                if val is not None:
                    campaign_str = str(val).strip()
                    if not campaign_str or campaign_str.lower() == "nan":
                        campaign_str = "Unknown Campaign"
                    virtual_sheet_name = f"{sheet_name} [{campaign_str}]"
                else:
                    virtual_sheet_name = sheet_name

                looks_lead = looks_like_lead_sheet(normalize_headers(group_df))
                detected = detect_sheet_property_type(filename, virtual_sheet_name)
                
                parsed.append(
                    {
                        "filename": filename,
                        "sheet_name": virtual_sheet_name,
                        "df": group_df,
                        "include": looks_lead,
                        "property_type": detected,
                    }
                )
                sheet_entries.append(
                    {
                        "sheet_index": len(parsed) - 1,
                        "sheet_name": virtual_sheet_name,
                        "row_count": len(group_df),
                        "looks_like_lead_sheet": looks_lead,
                        "detected_type": detected,
                        "include": looks_lead,
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
    }
    with REPORTS_LOCK:
        REPORTS[report_id] = entry
        while len(REPORTS) > MAX_REPORTS:  # evict oldest (insertion order)
            oldest = next(iter(REPORTS))
            del REPORTS[oldest]

    return {
        "report_id": report_id,
        "files": files_summary,
        "property_type_options": PROPERTY_TYPE_OPTIONS,
    }
