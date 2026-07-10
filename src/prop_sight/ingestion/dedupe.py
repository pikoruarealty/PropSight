"""Collapse the same lead appearing in several exports into one row.

Why this exists
---------------
The same person is exported by three unrelated systems (the legacy calling
workbooks, Privyr, and the CRM CSV). None of them share a lead ID; the only
field common to all is the mobile number. So the mobile number, normalized by
`phone.normalize_phone`, is the cross-format identity key.

Two rows are the same lead when they share BOTH a valid mobile and a matching
folded name. Phone alone is not enough: in the client's own data, 139 numbers
are shared by two or more genuinely different people (families using one
handset). Merging on phone alone would silently delete real leads.

Merging is a coalesce, not a "first row wins" drop. The legacy sheets carry
budget and configuration; Privyr carries campaign attribution and timestamps.
The same lead in both must end up with all of it, so per column we take the
first non-null value across the group, preferring the richest row.
"""

from __future__ import annotations

import pandas as pd

from .phone import name_key, normalize_phone_series, phone_status

# Bookkeeping columns added by this module (never treated as lead data).
PHONE_NORM = "phone_normalized"
MERGED_COUNT = "merged_row_count"
MERGED_SOURCES = "merged_sources"


def _richness(df: pd.DataFrame) -> pd.Series:
    """How many fields this row actually fills — used to pick the survivor row.

    Bookkeeping columns are excluded so they can't bias the ranking.
    """
    ignore = {PHONE_NORM, MERGED_COUNT, MERGED_SOURCES, "source_file", "source_sheet"}
    cols = [c for c in df.columns if c not in ignore]
    return df[cols].notna().sum(axis=1)


def _resolve_blank_names(frame: pd.DataFrame) -> pd.Series:
    """Attach unnamed rows to the sole named lead on the same phone, if there is one.

    A Privyr row can arrive with a phone but no name while the legacy sheet has
    both. When a phone has exactly one distinct real name, a blank-name row on
    that phone is that person. When it has two or more, the blank is genuinely
    ambiguous and stays its own lead rather than being guessed onto one of them.
    """
    keys = frame["_name_key"].copy()
    named = frame[keys != ""]
    sole = (
        named.groupby(PHONE_NORM)["_name_key"]
        .nunique()
        .loc[lambda s: s == 1]
        .index
    )
    if len(sole) == 0:
        return keys
    lookup = (
        named[named[PHONE_NORM].isin(sole)]
        .drop_duplicates(PHONE_NORM)
        .set_index(PHONE_NORM)["_name_key"]
    )
    blank = keys == ""
    keys.loc[blank] = frame.loc[blank, PHONE_NORM].map(lookup).fillna("")
    return keys


def dedupe_leads(df: pd.DataFrame, phone_col: str = "phone") -> tuple[pd.DataFrame, dict]:
    """Drop unusable rows, merge duplicate leads, and report what changed.

    Rows are partitioned three ways by `phone_status`:

      valid   -> normalized, grouped on (mobile, folded name), coalesced.
      invalid -> dropped. The cell holds something ('#REF!', '20-0010511', a
                 landline) that is not a reachable mobile, so the lead cannot
                 be called and cannot be matched to any other export.
      missing -> dropped. A lead nobody can call is not actionable, and with
                 no identity key it can never be matched to any other export
                 either — keeping it around only pads the row count.

    Returns (deduplicated_df, stats).
    """
    stats = {
        "input_rows": int(len(df)),
        "invalid_phone_dropped": 0,
        "missing_phone_dropped": 0,
        "duplicate_rows_merged": 0,
        "unique_leads": 0,
        "cross_file_leads": 0,
    }
    if df.empty or phone_col not in df.columns:
        stats["unique_leads"] = int(len(df))
        return df.copy(), stats

    work = df.copy()
    status = work[phone_col].map(phone_status)

    stats["invalid_phone_dropped"] = int((status == "invalid").sum())
    stats["missing_phone_dropped"] = int((status == "missing").sum())

    keyed = work[status == "valid"].copy()

    if keyed.empty:
        stats["unique_leads"] = 0
        return keyed.reset_index(drop=True), stats

    keyed[PHONE_NORM] = normalize_phone_series(keyed[phone_col])
    keyed["_name_key"] = keyed.get("name", pd.Series("", index=keyed.index)).map(name_key)
    keyed["_name_key"] = _resolve_blank_names(keyed)

    # A lead present in more than one export is the whole point of matching on
    # phone; count it before the groups collapse.
    if "source_file" in keyed.columns:
        per_phone_files = keyed.groupby(PHONE_NORM)["source_file"].nunique()
        stats["cross_file_leads"] = int((per_phone_files > 1).sum())

    # Richest row first, so GroupBy.first() — which skips nulls per column —
    # yields that row's values wherever several rows disagree, and backfills
    # every other column from whichever duplicate happened to have it.
    keyed["_richness"] = _richness(keyed)
    keyed = keyed.sort_values("_richness", ascending=False, kind="stable")

    group_cols = [PHONE_NORM, "_name_key"]
    grouped = keyed.groupby(group_cols, sort=False, dropna=False)

    merged = grouped.first().reset_index()
    merged[MERGED_COUNT] = grouped.size().reset_index(drop=True).values
    if "source_file" in keyed.columns:
        merged[MERGED_SOURCES] = (
            grouped["source_file"]
            .apply(lambda s: " · ".join(sorted(set(s.dropna().astype(str)))))
            .reset_index(drop=True)
            .values
        )

    stats["duplicate_rows_merged"] = int(len(keyed) - len(merged))

    merged = merged.drop(columns=["_name_key", "_richness"], errors="ignore")
    stats["unique_leads"] = int(len(merged))
    return merged.reset_index(drop=True), stats
