"""Broker-facing core: HWC flag, priority call list.

HWC semantics per the client: ANY non-empty value in the HWC column marks a
client that matters.
"""

from __future__ import annotations

import pandas as pd

from .common import clean_str, value_counts_list
from .budget import attach_budget_columns


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df.get(name, pd.Series(dtype=object, index=df.index))


def hwc_flag(df: pd.DataFrame) -> pd.Series:
    """True if HWC column is not empty OR client_status indicates Hot/Warm/Cold."""
    has_hwc = clean_str(_col(df, "hwc")).notna()
    client_stat = clean_str(_col(df, "client_status")).str.lower()
    # Safely handle NaN when using str accessors, use non-capturing group (?:) to avoid pandas warnings
    is_hwc_status = client_stat.fillna("").str.contains(r"\b(?:hot|warm|cold)\b", regex=True)
    return has_hwc | is_hwc_status


def core_summary(df: pd.DataFrame) -> dict:
    hwc = hwc_flag(df)
    total = len(df)
    return {
        "counts": {
            "total": total,
            "hwc": int(hwc.sum()),
            "hwc_share": round(float(hwc.mean()), 4) if total else 0,
        },
        "hwc_donut": [
            {"label": "HWC-marked", "count": int(hwc.sum())},
            {"label": "Not marked", "count": int(total - hwc.sum())},
        ],
        "hwc_raw_values": value_counts_list(_col(df, "hwc"), top=6),
    }


def action_list(df: pd.DataFrame, limit: int = 20) -> list[dict]:
    """Priority call list: HWC-marked clients, richest budget first."""
    if "budget_value" not in df.columns:
        df = attach_budget_columns(df)
    priority = df[hwc_flag(df)].copy()
    if priority.empty:
        return []
    priority["_budget_sort"] = pd.to_numeric(priority["budget_value"], errors="coerce")
    priority = priority.sort_values("_budget_sort", ascending=False, na_position="last")

    def cell(row, col):
        v = row.get(col)
        return str(v).strip() if pd.notna(v) and str(v).strip() else None

    out = []
    for _, row in priority.head(limit).iterrows():
        out.append(
            {
                "name": cell(row, "name") or "(no name)",
                "phone": cell(row, "phone"),
                "hwc": cell(row, "hwc"),
                "budget": cell(row, "budget"),
                "configuration": cell(row, "configuration_required"),
                "buying_status": cell(row, "buying_status"),
                "call_status": cell(row, "call_status"),
                "remarks": cell(row, "qualitative_remarks"),
            }
        )
    return out
