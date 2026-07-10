"""Free-text budget parsing + fixed Crore-band bucketing.

All parsed values are normalized to Lakh internally (1 Cr = 100 L), then
displayed in Crore since that is the natural unit for this market.

Bands are fixed to real-estate-meaningful ranges in Crore:
  < 1 Cr | 1–2 | 2–3 | 3–4 | 4–5 | 5–7 | 7–10 | 10+ Cr
"""

from __future__ import annotations

import re

import pandas as pd

from .common import clean_str, quality_rate

# Fixed bands in Lakh (budget_value is in Lakh internally).
# Each entry: (label, lower_lakh_inclusive, upper_lakh_exclusive)
_BANDS: list[tuple[str, float, float]] = [
    ("< 1 Cr",   0,    100),
    ("1–2 Cr",   100,  200),
    ("2–3 Cr",   200,  300),
    ("3–4 Cr",   300,  400),
    ("4–5 Cr",   400,  500),
    ("5–7 Cr",   500,  700),
    ("7–10 Cr",  700,  1000),
    ("10+ Cr",   1000, float("inf")),
]

_NUM_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(cr|crore|crores|c|l|lac|lacs|lakh|lakhs|k)?", re.IGNORECASE
)

# No individual homebuyer lead in this market is shopping above this. Above it,
# the "Cr"/"L" the row typed is almost certainly a typo or a unit mismatch, and
# a wrong number that big would swamp every average, max, and Meta export value
# it touches — so it is treated as unparseable rather than taken at face value.
_MAX_PLAUSIBLE_LAKH = 5000.0


def _to_lakh(num: float, unit: str | None) -> float:
    unit = (unit or "").lower()
    if unit in ("cr", "crore", "crores", "c"):
        return num * 100.0
    if unit in ("l", "lac", "lacs", "lakh", "lakhs"):
        return num
    if unit == "k":
        return num / 100.0
    # Bare number: infer scale. <=20 reads as Cr ("1.5"), <=1000 as Lakh
    # ("450"), anything bigger as raw rupees ("7500000").
    if num <= 20:
        return num * 100.0
    if num <= 1000:
        return num
    return num / 100_000.0


def parse_budget(text: object) -> float | None:
    """Parse one budget cell to a value in Lakh; ranges return their midpoint."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, (int, float)):
        return _to_lakh(float(text), None)
    s = str(text).strip()
    if not s:
        return None
    values: list[float] = []
    for num_s, unit in _NUM_UNIT.findall(s):
        num_s = num_s.replace(",", ".")
        try:
            num = float(num_s)
        except ValueError:
            continue
        if num <= 0:
            continue
        lakh = _to_lakh(num, unit or None)
        if lakh > _MAX_PLAUSIBLE_LAKH:
            continue
        values.append(lakh)
    if not values:
        return None
    return round((min(values) + max(values)) / 2, 2)


def _assign_band(lakh: float) -> str:
    for label, lo, hi in _BANDS:
        if lo <= lakh < hi:
            return label
    return "10+ Cr"


def attach_budget_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add budget_value (Lakh) and budget_bucket (fixed Crore band) columns."""
    out = df.copy()
    if "budget" in out.columns:
        out["budget_value"] = clean_str(out["budget"]).map(
            lambda v: parse_budget(v) if pd.notna(v) else None
        )
    else:
        out["budget_value"] = None
    values = pd.to_numeric(out["budget_value"], errors="coerce")
    out["budget_bucket"] = pd.NA
    valid_idx = values.dropna().index
    if len(valid_idx) > 0:
        out.loc[valid_idx, "budget_bucket"] = values[valid_idx].map(_assign_band)
    return out


def _cr(lakh: float) -> float:
    """Lakh -> Crore, rounded for display."""
    return round(lakh / 100.0, 2)


def budget_analysis(df: pd.DataFrame) -> dict:
    """Distribution stats + fixed Crore-band buckets with counts.

    Reported in Crore — real budgets at this scale read far more naturally
    than the equivalent Lakh figures.
    """
    if "budget_value" not in df.columns:
        df = attach_budget_columns(df)
    values = pd.to_numeric(df["budget_value"], errors="coerce")
    valid = values.dropna()
    total = len(df)
    result: dict = {
        "total_rows": total,
        "parsed_count": int(len(valid)),
        "unparsed_count": int(total - len(valid)),
        "unit": "Crore",
    }
    if valid.empty:
        result["buckets"] = []
        return result
    result.update(
        {
            "min": _cr(float(valid.min())),
            "max": _cr(float(valid.max())),
            "median": _cr(float(valid.median())),
            "mean": _cr(float(valid.mean())),
        }
    )
    buckets = []
    if "budget_bucket" in df.columns and df["budget_bucket"].notna().any():
        bucket_col = df["budget_bucket"]
        present = set(bucket_col.dropna())
        # Output bands in natural order, only those with data
        for label, lo, hi in _BANDS:
            if label not in present:
                continue
            mask = bucket_col.eq(label).fillna(False).astype(bool)
            sub = values[mask].dropna()
            buckets.append(
                {
                    "label": label,
                    "count": int(mask.sum()),
                    "min": _cr(float(sub.min())) if len(sub) else None,
                    "max": _cr(float(sub.max())) if len(sub) else None,
                    "quality_rate": quality_rate(df[mask]),
                }
            )
    result["buckets"] = buckets
    return result
