"""Merge all sheets of all uploaded workbooks into one tagged DataFrame."""

from __future__ import annotations

import pandas as pd

from .normalize import ensure_canonical_columns, normalize_headers


def _project_label(row: pd.Series) -> str:
    lead_for_project = row.get("lead_for_project")
    if pd.notna(lead_for_project) and str(lead_for_project).strip():
        return str(lead_for_project).strip()
    source_sheet = row.get("source_sheet")
    if pd.notna(source_sheet) and str(source_sheet).strip():
        return str(source_sheet).strip()
    return "Unknown"


def merge_sheets(sheets: list[dict]) -> pd.DataFrame:
    """Concatenate normalized sheets (each with its own property type).

    Each item in `sheets` is:
        {"filename": str, "sheet_name": str, "property_type": str, "df": DataFrame}

    Every row is tagged with source_file / source_sheet / property_type, and a
    `project` label (lead_for_project if non-empty, else the sheet name).
    """
    frames: list[pd.DataFrame] = []
    for item in sheets:
        df = normalize_headers(item["df"])
        df = ensure_canonical_columns(df)
        df["source_file"] = item["filename"]
        df["source_sheet"] = item["sheet_name"]
        df["property_type"] = item["property_type"]
        frames.append(df)
    if not frames:
        return ensure_canonical_columns(
            pd.DataFrame(columns=["source_file", "source_sheet", "property_type"])
        ).assign(project=pd.Series(dtype="object"))
    merged = pd.concat(frames, ignore_index=True)
    merged["project"] = merged.apply(_project_label, axis=1)
    return merged


def merge_workbooks(workbooks: list[dict]) -> pd.DataFrame:
    """Back-compat wrapper: one property type applied to every sheet in a file.

    Each item: {"filename": str, "property_type": str, "sheets": {name: DataFrame}}
    """
    return merge_sheets(
        [
            {
                "filename": wb["filename"],
                "sheet_name": sheet_name,
                "property_type": wb["property_type"],
                "df": sheet_df,
            }
            for wb in workbooks
            for sheet_name, sheet_df in wb["sheets"].items()
        ]
    )
