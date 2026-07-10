"""Merge all sheets of all uploaded workbooks into one tagged DataFrame."""

from __future__ import annotations

import pandas as pd

from .normalize import ensure_canonical_columns, normalize_headers
from .notes import extract_note_fields
from .schema_llm import apply_mapping, infer_note_label_mapping


def _project_label(row: pd.Series) -> str:
    lead_for_project = row.get("lead_for_project")
    if pd.notna(lead_for_project) and str(lead_for_project).strip():
        return str(lead_for_project).strip()
    source_sheet = row.get("source_sheet")
    if pd.notna(source_sheet) and str(source_sheet).strip():
        return str(source_sheet).strip()
    return "Unknown"


def prepare_sheet(item: dict) -> tuple[pd.DataFrame, dict]:
    """Bring one sheet to canonical shape: rename, lift notes, fill gaps.

    Order matters. Headers are renamed first (LLM mapping, then the alias
    table), so `extract_note_fields` can find the notes column under its
    canonical name. Notes are lifted before `ensure_canonical_columns` so a
    field that exists *only* inside the prose still lands in a real column
    rather than an all-empty one.
    """
    df = apply_mapping(item["df"], item.get("column_mapping") or {})
    df = normalize_headers(df)
    df, notes_info = extract_note_fields(df, infer_note_label_mapping(df))
    df = ensure_canonical_columns(df)
    return df, notes_info


def merge_sheets(sheets: list[dict]) -> pd.DataFrame:
    """Concatenate normalized sheets (each with its own property type).

    Each item in `sheets` is:
        {"filename": str, "sheet_name": str, "property_type": str, "df": DataFrame,
         "column_mapping": dict | None}

    `column_mapping` is an LLM-inferred rename for export formats the alias
    table does not recognize; it is applied before normalization so that both
    paths converge on the same canonical names.

    Every row is tagged with source_file / source_sheet / property_type, and a
    `project` label (lead_for_project if non-empty, else the sheet name).
    """
    frames: list[pd.DataFrame] = []
    for item in sheets:
        df, _ = prepare_sheet(item)
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
