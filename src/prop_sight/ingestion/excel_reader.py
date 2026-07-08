"""Read an uploaded .xlsx workbook fully in memory."""

from __future__ import annotations

import io

import pandas as pd


def read_workbook(contents: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Read every sheet of a workbook from raw bytes (supports .xlsx and .csv).

    Returns {sheet_name: DataFrame}. Rows that are entirely empty are dropped;
    sheets left with zero rows are removed.
    """
    if filename.lower().endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(contents))
            sheets = {filename: df}
        except Exception:
            # Fallback to different encoding if utf-8 fails
            df = pd.read_csv(io.BytesIO(contents), encoding="latin1")
            sheets = {filename: df}
    else:
        sheets = pd.read_excel(io.BytesIO(contents), sheet_name=None, engine="openpyxl")
        
    cleaned: dict[str, pd.DataFrame] = {}
    for name, df in sheets.items():
        df = df.dropna(how="all")
        if not df.empty:
            cleaned[name] = df.reset_index(drop=True)
    return cleaned
