"""Bundle focused analytics into one JSON-serializable report.

Columns of interest:
  Primary  : budget, configuration_required, hwc
  Secondary: call_status (latest_call_status), buying_status
  AI input : qualitative_remarks (sampled raw text fed to the LLM)
"""

from __future__ import annotations

import pandas as pd

from . import core, segmentation
from .budget import attach_budget_columns, budget_analysis
from .common import clean_str, crosstab_dict, quality_rate, to_jsonable, value_counts_list


# ── helpers ──────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df.get(name, pd.Series(dtype=object, index=df.index))


def _meta(df: pd.DataFrame) -> dict:
    property_types = (
        sorted(df["property_type"].dropna().astype(str).unique().tolist())
        if "property_type" in df.columns
        else []
    )
    return {
        "total_leads": int(len(df)),
        "quality_rate": quality_rate(df),
        "property_types": property_types,
        "source_files": (
            sorted(df["source_file"].dropna().astype(str).unique().tolist())
            if "source_file" in df.columns
            else []
        ),
    }


# ── secondary distributions ──────────────────────────────────────────────────

def call_status_breakdown(df: pd.DataFrame) -> dict:
    """Distribution of Latest Call Status values."""
    col = _col(df, "call_status")
    return {"distribution": value_counts_list(col)}


def buying_status_breakdown(df: pd.DataFrame) -> dict:
    """Distribution of Buying Status values."""
    col = _col(df, "buying_status")
    return {"distribution": value_counts_list(col)}


# ── cross-tabs ───────────────────────────────────────────────────────────────

def config_x_budget(df: pd.DataFrame) -> dict:
    """crosstab(configuration_required, budget_bucket) — which BHK sits where."""
    if "budget_bucket" not in df.columns:
        df = attach_budget_columns(df)
    config = _col(df, "configuration_required")
    return crosstab_dict(config, df["budget_bucket"])


def hwc_x_config(df: pd.DataFrame) -> dict:
    """crosstab(hwc_label, configuration_required) — what VIP leads want."""
    from .core import hwc_flag
    hwc_label = hwc_flag(df).map({True: "HWC-marked", False: "Not marked"})
    config = clean_str(_col(df, "configuration_required"))
    return crosstab_dict(hwc_label, config)


def hwc_x_budget(df: pd.DataFrame) -> dict:
    """crosstab(hwc_label, budget_bucket) — do HWC leads have higher budgets?"""
    from .core import hwc_flag
    if "budget_bucket" not in df.columns:
        df = attach_budget_columns(df)
    hwc_label = hwc_flag(df).map({True: "HWC-marked", False: "Not marked"})
    return crosstab_dict(hwc_label, df["budget_bucket"])


def buying_status_x_config(df: pd.DataFrame) -> dict:
    """crosstab(buying_status, configuration_required)."""
    buying = _col(df, "buying_status")
    config = clean_str(_col(df, "configuration_required"))
    return crosstab_dict(buying, config)


def call_status_x_buying(df: pd.DataFrame) -> dict:
    """crosstab(call_status, buying_status) — are unreachable leads warm?"""
    call = _col(df, "call_status")
    buying = _col(df, "buying_status")
    return crosstab_dict(call, buying)


# ── report bundler ────────────────────────────────────────────────────────────

def full_report(df: pd.DataFrame) -> dict:
    """Run focused analytics on the (optionally pre-filtered) DataFrame."""
    df = attach_budget_columns(df)
    
    # Clean up common typo: "3K to 4K" -> "3 BHK to 4 BHK"
    if "configuration_required" in df.columns:
        mask = df["configuration_required"].notna()
        if mask.any():
            df.loc[mask, "configuration_required"] = (
                df.loc[mask, "configuration_required"]
                .astype(str)
                .str.replace(r"(\d+)\s*[kK]\b", r"\1 BHK", regex=True)
            )
            
    report = {
        "meta": _meta(df),
        # core — HWC only
        "core": core.core_summary(df),
        "action_list": core.action_list(df),
        # primary columns
        "budget": budget_analysis(df),
        "configuration": segmentation.configuration_preference_breakdown(df),
        # secondary columns
        "call_status": call_status_breakdown(df),
        "buying_status": buying_status_breakdown(df),
        # cross-tabs
        "config_x_budget": config_x_budget(df),
        "hwc_x_config": hwc_x_config(df),
        "hwc_x_budget": hwc_x_budget(df),
        "buying_status_x_config": buying_status_x_config(df),
        "call_status_x_buying": call_status_x_buying(df),
    }
    return to_jsonable(report)
