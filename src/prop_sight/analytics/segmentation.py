"""Configuration preference breakdown only."""

from __future__ import annotations

import pandas as pd

from .common import clean_str, value_counts_list


def configuration_preference_breakdown(df: pd.DataFrame) -> dict:
    config = df.get("configuration_required", pd.Series(dtype=object, index=df.index))
    return {"overall": value_counts_list(config)}
