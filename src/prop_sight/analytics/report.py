"""Bundle focused analytics into one JSON-serializable report.

Columns of interest:
  Primary  : budget, configuration_required, hwc
  Secondary: call_status (latest_call_status), buying_status
  Timing   : date / first_call_date -> weekday & hour of arrival
  AI input : qualitative_remarks (sampled raw text fed to the LLM)

Every section carries enough metadata for the dashboard to decide whether to
render it at all. A chart with no data must be hidden, not drawn empty, and a
chart computed from a minority of leads must say so — see `crosstab_dict`.
"""

from __future__ import annotations

import re

import pandas as pd

from . import core, rules, segmentation, timeseries
from .budget import attach_budget_columns, budget_analysis
from .synonyms import STATUS_SYNONYMS, apply_configuration_synonyms, apply_status_synonyms
from .common import (
    clean_str,
    crosstab_dict,
    fold_case_variants,
    quality_rate,
    to_jsonable,
    value_counts_list,
)


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


# ── data quality ─────────────────────────────────────────────────────────────

# The fields the dashboard actually charts. Their fill rate is the ceiling on
# how much of the dataset any chart involving them can possibly represent.
_TRACKED_FIELDS = [
    "phone", "name", "budget", "configuration_required", "hwc",
    "call_status", "buying_status", "qualitative_remarks", "date",
]


def data_quality(df: pd.DataFrame) -> dict:
    """Per-field fill rates, so a low-coverage chart can be explained, not guessed at.

    This is the answer to "the chart says 112 but I uploaded 5,000 leads": the
    crosstab could only use leads that had both fields, and most have neither.
    """
    total = len(df)
    fields = []
    for field in _TRACKED_FIELDS:
        if field not in df.columns:
            continue
        filled = int(clean_str(df[field]).notna().sum())
        fields.append(
            {
                "field": field,
                "filled": filled,
                "missing": int(total - filled),
                "fill_rate": round(filled / total, 4) if total else 0,
            }
        )
    result = {"total_leads": total, "fields": fields}

    if "merged_row_count" in df.columns:
        counts = pd.to_numeric(df["merged_row_count"], errors="coerce").fillna(1)
        result["merged_leads"] = int((counts > 1).sum())
        result["rows_absorbed"] = int(counts.sum() - len(counts))
    return result


# ── secondary distributions ──────────────────────────────────────────────────

def call_status_breakdown(df: pd.DataFrame) -> dict:
    """Distribution of Latest Call Status values."""
    return {"distribution": value_counts_list(_col(df, "call_status"))}


def buying_status_breakdown(df: pd.DataFrame) -> dict:
    """Distribution of Buying Status values."""
    return {"distribution": value_counts_list(_col(df, "buying_status"))}


# ── marketing analytics ──────────────────────────────────────────────────────

def marketing_analysis(df: pd.DataFrame) -> dict:
    """Lead distribution by Facebook Page, Campaign, and Ad Set."""
    return {
        "pages": value_counts_list(_col(df, "facebook_page")),
        "campaigns": value_counts_list(_col(df, "campaign")),
        "ad_sets": value_counts_list(_col(df, "facebook_ad_set")),
    }


# ── cross-tabs ───────────────────────────────────────────────────────────────

def config_x_budget(df: pd.DataFrame) -> dict:
    """crosstab(configuration_required, budget_bucket) — which BHK sits where."""
    if "budget_bucket" not in df.columns:
        df = attach_budget_columns(df)
    return crosstab_dict(_col(df, "configuration_required"), df["budget_bucket"])


def _hwc_label(df: pd.DataFrame) -> pd.Series:
    return core.hwc_flag(df).map({True: "HWC-marked", False: "Not marked"})


def hwc_x_config(df: pd.DataFrame) -> dict:
    """crosstab(hwc_label, configuration_required) — what VIP leads want."""
    return crosstab_dict(_hwc_label(df), clean_str(_col(df, "configuration_required")))


def hwc_x_budget(df: pd.DataFrame) -> dict:
    """crosstab(hwc_label, budget_bucket) — do HWC leads have higher budgets?"""
    if "budget_bucket" not in df.columns:
        df = attach_budget_columns(df)
    return crosstab_dict(_hwc_label(df), df["budget_bucket"])


def buying_status_x_config(df: pd.DataFrame) -> dict:
    """crosstab(buying_status, configuration_required)."""
    return crosstab_dict(_col(df, "buying_status"), clean_str(_col(df, "configuration_required")))


def call_status_x_buying(df: pd.DataFrame) -> dict:
    """crosstab(call_status, buying_status) — are unreachable leads warm?"""
    return crosstab_dict(_col(df, "call_status"), _col(df, "buying_status"))


# ── lead classification ──────────────────────────────────────────────────────

def _class_label(df: pd.DataFrame) -> pd.Series:
    """Good/Bad per lead; unclassified becomes <NA> so charts ignore it.

    "Unclassified" is not a kind of lead — it is the absence of a verdict, almost
    always because the fields a rule needs were never filled in. Plotting it as a
    third category would invite the reader to compare it with Good and Bad, which
    means nothing. It is reported as a count and a set of reasons instead, and
    `crosstab_dict`'s coverage caption already explains the shortfall.
    """
    if rules.CLASS_COL not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="string")
    labels = df[rules.CLASS_COL].map(rules.CLASS_LABELS)
    return clean_str(labels).mask(df[rules.CLASS_COL] == rules.UNCLASSIFIED)


def lead_class_breakdown(df: pd.DataFrame) -> dict:
    return {"distribution": value_counts_list(_class_label(df))}


def lead_class_x_budget(df: pd.DataFrame) -> dict:
    """crosstab(lead_class, budget_bucket) — do good leads carry bigger budgets?"""
    if "budget_bucket" not in df.columns:
        df = attach_budget_columns(df)
    return crosstab_dict(_class_label(df), df["budget_bucket"])


def lead_class_x_config(df: pd.DataFrame) -> dict:
    """crosstab(lead_class, configuration_required) — what do good leads want?"""
    return crosstab_dict(_class_label(df), clean_str(_col(df, "configuration_required")))


def lead_class_x_call_status(df: pd.DataFrame) -> dict:
    """crosstab(lead_class, call_status) — are good leads the ones we reach?"""
    return crosstab_dict(_class_label(df), _col(df, "call_status"))


# ── availability ─────────────────────────────────────────────────────────────

def _has_rows(section: dict | None, key: str) -> bool:
    return bool(section and section.get(key))


def _classification_available(report: dict) -> bool:
    """Only surface classification once a rule has actually classified something.

    Rules that exist but match nobody would otherwise add four empty cards.
    """
    summary = report.get("classification") or {}
    return bool(summary.get("enabled")) and bool(summary.get("good") or summary.get("bad"))


def _availability(report: dict) -> dict:
    """Which dashboard sections have data, so the UI hides the rest.

    An empty chart is worse than no chart: it reads as "we have no leads in this
    category" when it actually means "this export format never had that column".
    """
    marketing = report.get("marketing", {})
    classified = _classification_available(report)
    return {
        "classification": classified,
        "lead_class_x_budget": classified and _has_rows(report.get("lead_class_x_budget"), "rows"),
        "lead_class_x_config": classified and _has_rows(report.get("lead_class_x_config"), "rows"),
        "lead_class_x_call_status": classified and _has_rows(report.get("lead_class_x_call_status"), "rows"),
        "budget": _has_rows(report.get("budget"), "buckets"),
        "configuration": _has_rows(report.get("configuration"), "overall"),
        "call_status": _has_rows(report.get("call_status"), "distribution"),
        "buying_status": _has_rows(report.get("buying_status"), "distribution"),
        "hwc": bool(report.get("core", {}).get("counts", {}).get("hwc")),
        "marketing": any(bool(marketing.get(k)) for k in ("pages", "campaigns", "ad_sets")),
        "marketing_pages": bool(marketing.get("pages")),
        "marketing_campaigns": bool(marketing.get("campaigns")),
        "marketing_ad_sets": bool(marketing.get("ad_sets")),
        "time": bool(report.get("time", {}).get("available")),
        "time_of_day": bool(report.get("time", {}).get("has_time_of_day")),
        "config_x_budget": _has_rows(report.get("config_x_budget"), "rows"),
        "hwc_x_config": _has_rows(report.get("hwc_x_config"), "rows"),
        "hwc_x_budget": _has_rows(report.get("hwc_x_budget"), "rows"),
        "buying_status_x_config": _has_rows(report.get("buying_status_x_config"), "rows"),
        "call_status_x_buying": _has_rows(report.get("call_status_x_buying"), "rows"),
    }


# ── report bundler ────────────────────────────────────────────────────────────

_BHK_SPACED = re.compile(r"(?i)(\d+)\s*bhk")
# Reps type '3K' / '4 K' for '3BHK'. Applied after the BHK rule so it cannot
# fire on the 'B' of an already-correct '4BHK'.
_BHK_SHORTHAND = re.compile(r"(?i)\b(\d+)\s*k\b")


def normalize_configuration(value: object) -> object:
    """Fold the spellings of one unit-type requirement into a single label.

    '4 BHK', '4bhk' and '4BHK' are one requirement typed three ways. Left alone
    they become three donut slices and three crosstab columns, splitting the
    single most important number on the dashboard — 4BHK demand — across rows
    that look like distinct categories.

    Multi-select cells ('4 BHK, 5 BHK') keep both values as one compound label:
    a lead open to either is genuinely not the same as a lead who wants only one.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return value
    text = str(value).strip()
    if not text:
        return value
    text = _BHK_SPACED.sub(lambda m: f"{m.group(1)}BHK", text)
    text = _BHK_SHORTHAND.sub(lambda m: f"{m.group(1)}BHK", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_configuration(df: pd.DataFrame) -> pd.DataFrame:
    if "configuration_required" not in df.columns:
        return df
    df["configuration_required"] = df["configuration_required"].map(normalize_configuration)
    return df


# Free-text columns reps type by hand, where casing drifts between entries.
_CASE_FOLDED_FIELDS = [
    "call_status", "buying_status", "visit_status", "interest_level",
    "configuration_required", "stage", "purpose_of_buying",
]


def _fold_labels(df: pd.DataFrame) -> pd.DataFrame:
    for field in _CASE_FOLDED_FIELDS:
        if field in df.columns:
            df[field] = fold_case_variants(df[field])

    # Then the wording variants ('Bungalows' vs 'Bungalow only'), which case
    # folding cannot see. Case folding runs first so the synonym table only has
    # to list one spelling of each phrase.
    if "configuration_required" in df.columns:
        df["configuration_required"] = apply_configuration_synonyms(df["configuration_required"])
    for field in STATUS_SYNONYMS:
        if field in df.columns:
            df[field] = apply_status_synonyms(df[field], field)
    return df


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Derive budget bands and fold every label the dashboard will display.

    Anything that reads or filters on field *values* — the charts, the rule
    engine, the rule editor's value dropdowns — must run on this frame, or they
    disagree about what a value is called. A rule listing "Bungalow" has to match
    the row that arrived as "Bungalow only", and `budget_bucket` does not exist
    until `attach_budget_columns` derives it.
    """
    df = attach_budget_columns(df)
    df = _clean_configuration(df)
    # After configuration spellings are folded, so 'Bhk' vs 'BHK' cannot resurface.
    return _fold_labels(df)


def classify_frame(df: pd.DataFrame, rule_list: list[dict] | None) -> pd.DataFrame:
    """Prepare then classify — the exact frame `full_report` classifies."""
    prepared = prepare_frame(df)
    return rules.classify(prepared, rule_list or [])


def full_report(df: pd.DataFrame, rule_list: list[dict] | None = None) -> dict:
    """Run focused analytics on the (optionally pre-filtered) DataFrame.

    `rule_list` classifies each lead as good/bad/unclassified. Classification
    runs *after* label folding and budget bucketing, so a rule can name
    `budget_bucket` and can list "Bungalow" without also having to list
    "Bungalow only" — the rule sees exactly the values the dashboard shows.
    """
    df = prepare_frame(df)

    if rule_list:
        df = rules.classify(df, rule_list)

    report = {
        "meta": _meta(df),
        "classification": rules.classification_summary(df),
        "lead_class": lead_class_breakdown(df),
        "lead_class_x_budget": lead_class_x_budget(df),
        "lead_class_x_config": lead_class_x_config(df),
        "lead_class_x_call_status": lead_class_x_call_status(df),
        "data_quality": data_quality(df),
        # core — HWC only
        "core": core.core_summary(df),
        "action_list": core.action_list(df),
        # primary columns
        "budget": budget_analysis(df),
        "configuration": segmentation.configuration_preference_breakdown(df),
        # secondary columns
        "call_status": call_status_breakdown(df),
        "buying_status": buying_status_breakdown(df),
        # timing
        "time": timeseries.time_analysis(df),
        # marketing
        "marketing": marketing_analysis(df),
        # cross-tabs
        "config_x_budget": config_x_budget(df),
        "hwc_x_config": hwc_x_config(df),
        "hwc_x_budget": hwc_x_budget(df),
        "buying_status_x_config": buying_status_x_config(df),
        "call_status_x_buying": call_status_x_buying(df),
    }
    report["availability"] = _availability(report)
    return to_jsonable(report)
