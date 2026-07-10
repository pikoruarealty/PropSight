"""Business-vocabulary synonyms: different words, one meaning.

`fold_case_variants` in `common.py` merges values that differ only in casing.
This module handles the harder case — values that differ in *wording*:

    'Bungalows' / 'Bungalow only'        -> one unit type
    'Postponed' / 'Postponed for now'    -> one pipeline state
    'Not intrested'                      -> a typo for 'Not interested'

These cannot be folded automatically without a judgment about what the sales
team means, so they live here as an explicit, reviewable table rather than as a
similarity heuristic that would happily merge 'Postponed' with 'Not interested'.

Configuration is folded per *token*, not per value: 'Bungalows, 5BHK' is a
multi-select cell, and only its first token is a synonym. Statuses are folded on
the whole value, since a status cell holds exactly one state.
"""

from __future__ import annotations

import re

import pandas as pd

from .common import clean_str


def _key(text: str) -> str:
    """Match key: lowercase, punctuation and spacing collapsed."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# Unit types. Keys are matched per comma-separated token.
CONFIGURATION_SYNONYMS: dict[str, str] = {
    "bungalow": "Bungalow",
    "bungalows": "Bungalow",
    "bungalow only": "Bungalow",
    "villa": "Bungalow",
    "plot": "Plot",
    "plots": "Plot",
    "land": "Plot",
    "land plot": "Plot",          # 'Land & plot' -> key 'land plot'
    "land and plot": "Plot",
    "penthouse": "Penthouse",
    "penthouses": "Penthouse",
    "duplex": "Duplex",
    "duplexes": "Duplex",
    "commercial": "Commercial",
}

# Pipeline states. Keys are matched against the whole cell value.
STATUS_SYNONYMS: dict[str, dict[str, str]] = {
    "buying_status": {
        "postponed": "Postponed",
        "postponed for now": "Postponed",
        "postpone": "Postponed",
        "not intrested": "Not interested",   # typo in the source data
        "not interested": "Not interested",
        "still searching": "Still searching",
        "searching": "Still searching",
        "bought already": "Bought already",
        "already bought": "Bought already",
        "interested": "Interested",
    },
    "call_status": {
        "spoken": "Spoken",
        "not spoken": "Not spoken",
        "call back later": "Call back later",
        "callback later": "Call back later",
        "call later": "Call back later",
    },
}


def _fold_value(value: object, table: dict[str, str]) -> object:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return value
    return table.get(_key(str(value)), value)


def _fold_tokens(value: object, table: dict[str, str]) -> object:
    """Fold each comma-separated token, preserving the compound label."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return value
    tokens = [t.strip() for t in str(value).split(",") if t.strip()]
    if not tokens:
        return value
    folded = [table.get(_key(t), t) for t in tokens]
    # A cell like 'Bungalows, Bungalow only' collapses to a single token.
    deduped = list(dict.fromkeys(folded))
    return ", ".join(deduped)


def apply_configuration_synonyms(series: pd.Series) -> pd.Series:
    return clean_str(series).map(lambda v: _fold_tokens(v, CONFIGURATION_SYNONYMS))


def apply_status_synonyms(series: pd.Series, field: str) -> pd.Series:
    table = STATUS_SYNONYMS.get(field)
    if not table:
        return series
    return clean_str(series).map(lambda v: _fold_value(v, table))
