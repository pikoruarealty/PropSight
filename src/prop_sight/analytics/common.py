"""Helpers shared across analytics modules.

Nothing here hardcodes this client's field vocabularies — quality is derived
from generic warm/cold keyword signals over whatever values are observed.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Negative signals are checked FIRST so "Not Interested" never matches "interested".
_NEGATIVE_TOKENS = [
    "not interested", "no interest", "not looking", "cold", "lost", "dead",
    "junk", "invalid", "drop", "dnd", "wrong", "cancel", "postpone", "low",
    "no response", "not picking", "not reachable", "switched off",
]
_POSITIVE_TOKENS = [
    "hot", "warm", "high", "interested", "booked", "booking", "closed", "won",
    "finali", "ready", "confirmed", "negoti", "token", "visit done", "medium",
]


def clean_str(series: pd.Series) -> pd.Series:
    """Stripped string values; empty / NaN / 'nan' become <NA>."""
    s = series.astype("string").str.strip()
    return s.mask(s.isin(["", "nan", "None", "NaT", "-", "NA", "N/A"]) | s.isna())


def _text_is_warm(text: str) -> bool:
    low = text.lower()
    if any(tok in low for tok in _NEGATIVE_TOKENS):
        return False
    return any(tok in low for tok in _POSITIVE_TOKENS)


def is_quality(df: pd.DataFrame) -> pd.Series:
    """Boolean per row: does Buying Status or Interest Level look warm-or-better?

    Vocabulary is unknown per client, so this keyword-matches observed values
    (negatives checked before positives).
    """
    result = pd.Series(False, index=df.index)
    for col in ("buying_status", "interest_level"):
        if col not in df.columns:
            continue
        vals = clean_str(df[col])
        result |= vals.map(lambda v: _text_is_warm(v) if pd.notna(v) else False).astype(bool)
    return result


def quality_rate(df: pd.DataFrame) -> float | None:
    if len(df) == 0:
        return None
    return round(float(is_quality(df).mean()), 4)


def fold_case_variants(series: pd.Series) -> pd.Series:
    """Merge values that differ only in casing or surrounding whitespace.

    Reps type free text into status columns, so 'Call back later' and
    'Call Back Later' arrive as two categories describing one outcome. Split
    like that they show up as two donut slices and two crosstab rows.

    The surviving spelling is the one written most often (ties broken
    alphabetically for determinism), so the label shown is the one the team
    actually uses rather than whichever row happened to sort first.
    """
    values = clean_str(series)
    folded = values.str.lower()

    counts = pd.DataFrame({"value": values, "key": folded}).dropna()
    if counts.empty:
        return values

    ranked = (
        counts.groupby(["key", "value"]).size().reset_index(name="n")
        .sort_values(["key", "n", "value"], ascending=[True, False, True])
    )
    canonical = ranked.drop_duplicates("key").set_index("key")["value"]
    return folded.map(canonical).astype("string")


def value_counts_list(series: pd.Series, top: int | None = None) -> list[dict]:
    """[{value, count, share}] sorted by count desc; NaN/empty excluded."""
    vals = clean_str(series).dropna()
    total = len(vals)
    counts = vals.value_counts()
    if top:
        counts = counts.head(top)
    return [
        {"value": str(v), "count": int(c), "share": round(c / total, 4) if total else 0}
        for v, c in counts.items()
    ]


def crosstab_dict(rows: pd.Series, cols: pd.Series) -> dict:
    """JSON-friendly crosstab plus the coverage it was computed from.

    A crosstab can only count rows where BOTH variables are recorded. In this
    dataset most leads have neither a configuration nor a budget filled in, so
    a chart drawn from the matrix alone silently represents a small minority of
    leads while looking like it represents all of them — and two charts sharing
    an axis disagree on that axis's totals because each drops a different set of
    rows.

    `coverage` makes that explicit so the UI can caption every crosstab with the
    denominator its numbers actually refer to:
        {counted, total, missing_rows, missing_cols}
    """
    r = clean_str(rows)
    c = clean_str(cols)
    total = int(len(r))
    mask = r.notna() & c.notna()
    coverage = {
        "counted": int(mask.sum()),
        "total": total,
        "missing_rows": int(r.isna().sum()),
        "missing_cols": int(c.isna().sum()),
    }
    if not mask.any():
        return {"rows": [], "cols": [], "matrix": [], "coverage": coverage}
    ct = pd.crosstab(r[mask], c[mask])
    # Order both axes by marginal totals (largest first) for readable charts.
    ct = ct.loc[
        ct.sum(axis=1).sort_values(ascending=False).index,
        ct.sum(axis=0).sort_values(ascending=False).index,
    ]
    return {
        "rows": [str(v) for v in ct.index],
        "cols": [str(v) for v in ct.columns],
        "matrix": [[int(x) for x in row] for row in ct.values],
        "coverage": coverage,
    }


def to_jsonable(obj):
    """Recursively convert numpy/pandas scalars to plain Python; NaN -> None."""
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if math.isnan(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if obj is pd.NA or obj is pd.NaT:
        return None
    return obj
